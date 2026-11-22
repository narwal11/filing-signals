"""Postgres storage: create the schema and load data/filings.jsonl into it.

Usage:
    python db.py --check                  # connect and print the server version; changes nothing
    python db.py --create-schema          # run schema.sql (safe to repeat)
    python db.py --load [data/filings.jsonl]

The connection string comes from DATABASE_URL in .env (loaded with python-dotenv).
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

import psycopg
from dotenv import load_dotenv

import classify

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_JSONL = Path(__file__).with_name("data") / "filings.jsonl"


def connect() -> psycopg.Connection:
    load_dotenv()
    # autocommit=True: each `with conn.transaction()` block is then a real BEGIN/COMMIT.
    # Without it, the first query opens an implicit transaction and every later block
    # is only a savepoint inside it (psycopg docs, "Transaction contexts").
    # prepare_threshold=None turns off server-side prepared statements. Supabase's
    # transaction pooler can't keep them between transactions.
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None)


def create_schema(conn: psycopg.Connection) -> None:
    # schema.sql takes no parameters, so psycopg can send all its statements in one call.
    with conn.transaction():
        conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


# ---- JSONL record -> table rows (pure functions, unit tested) --------------


def record_to_rows(record: dict) -> dict:
    """Turn one filings.jsonl record into the rows for each table.

    Returns {"company": (cik, name), "ticker": (cik, ticker), "filing": {...}, "sections": [{...}, ...]}.
    """
    filing = record["filing"]
    # One call decides both filing flags and the pointer kinds, the same way miss_report.py does.
    result = classify.check_filing(record)  # None for filings it doesn't check
    non_standard = result is not None and result["outcome"] == classify.NON_STANDARD
    pointers = result["pointers"] if result else {}  # key -> (kind, text)

    filing_row = {
        "accession": filing["accession"],
        "cik": filing["cik"],
        "form": filing["form"],
        "filed_date": date.fromisoformat(filing["filed_date"]),
        "accepted_at": datetime.fromisoformat(filing["accepted_at"]),
        "accepted_at_raw": filing.get("accepted_at_raw"),  # missing in records ingested before the backfill
        "report_date": date.fromisoformat(filing["report_date"]) if filing["report_date"] else None,
        "sec_items": list(filing["items"]),
        "items_found": list(record.get("item_keys_found") or []),
        "primary_document": filing["primary_document"],
        "layout": "non_standard" if non_standard else "standard",
        "sec_metadata_mismatch": bool(result and result["mismatch"]),
    }
    section_rows = [
        {
            "accession": filing["accession"],
            "key": s["key"],
            "title": s["title"],
            "heading": s.get("heading", ""),
            "source": s["source"],
            "text": s["text"],
            "text_sha256": hashlib.sha256(s["text"].encode("utf-8")).digest(),
            # char_count is not sent: Postgres generates it from text.
            "pointer_kind": pointers[s["key"]][0] if s["key"] in pointers else None,
        }
        for s in record["sections"]
    ]
    return {
        "company": (filing["cik"], filing["company"]),
        "ticker": (filing["cik"], filing["ticker"]),
        "filing": filing_row,
        "sections": section_rows,
    }


# ---- writing ----------------------------------------------------------------

# Every upsert has a WHERE ... IS DISTINCT FROM clause: if nothing changed, the row is not
# rewritten. Reloading the same file then writes nothing, which keeps the database from
# filling up with dead copies of 94 MB of text.

UPSERT_COMPANY = """
    INSERT INTO companies (cik, name) VALUES (%s, %s)
    ON CONFLICT (cik) DO UPDATE SET name = EXCLUDED.name
    WHERE companies.name IS DISTINCT FROM EXCLUDED.name
"""

# valid_from is NULL (unknown) for now; NULLS NOT DISTINCT makes a repeat insert a conflict.
INSERT_TICKER = """
    INSERT INTO tickers (cik, ticker) VALUES (%s, %s)
    ON CONFLICT DO NOTHING
"""

FILING_COLUMNS = [
    "accession", "cik", "form", "filed_date", "accepted_at", "accepted_at_raw", "report_date",
    "sec_items", "items_found", "primary_document", "layout", "sec_metadata_mismatch",
]
UPSERT_FILING = """
    INSERT INTO filings (accession, cik, form, filed_date, accepted_at, accepted_at_raw, report_date,
                         sec_items, items_found, primary_document, layout, sec_metadata_mismatch)
    VALUES (%(accession)s, %(cik)s, %(form)s, %(filed_date)s, %(accepted_at)s, %(accepted_at_raw)s,
            %(report_date)s, %(sec_items)s, %(items_found)s, %(primary_document)s, %(layout)s,
            %(sec_metadata_mismatch)s)
    ON CONFLICT (accession) DO UPDATE SET
        cik = EXCLUDED.cik,
        form = EXCLUDED.form,
        filed_date = EXCLUDED.filed_date,
        accepted_at = EXCLUDED.accepted_at,
        -- Never replace a known raw timestamp with NULL from a record ingested before the backfill.
        accepted_at_raw = COALESCE(EXCLUDED.accepted_at_raw, filings.accepted_at_raw),
        report_date = EXCLUDED.report_date,
        sec_items = EXCLUDED.sec_items,
        items_found = EXCLUDED.items_found,
        primary_document = EXCLUDED.primary_document,
        layout = EXCLUDED.layout,
        sec_metadata_mismatch = EXCLUDED.sec_metadata_mismatch,
        loaded_at = now()  -- when this row last changed
    WHERE (filings.cik, filings.form, filings.filed_date, filings.accepted_at, filings.accepted_at_raw,
           filings.report_date, filings.sec_items, filings.items_found, filings.primary_document,
           filings.layout, filings.sec_metadata_mismatch)
          IS DISTINCT FROM
          (EXCLUDED.cik, EXCLUDED.form, EXCLUDED.filed_date, EXCLUDED.accepted_at,
           COALESCE(EXCLUDED.accepted_at_raw, filings.accepted_at_raw),
           EXCLUDED.report_date, EXCLUDED.sec_items, EXCLUDED.items_found, EXCLUDED.primary_document,
           EXCLUDED.layout, EXCLUDED.sec_metadata_mismatch)
"""

SECTION_COLUMNS = ["accession", "key", "title", "heading", "source", "text", "text_sha256", "pointer_kind"]
# Upsert, not delete-and-insert, so a section keeps its id across reloads
# (extractions and citations point at that id). char_count is a generated column,
# so Postgres recomputes it whenever text changes. The WHERE compares the 32-byte
# hash instead of the full text to decide whether the text changed.
UPSERT_SECTION = """
    INSERT INTO sections (accession, key, title, heading, source, text, text_sha256, pointer_kind)
    VALUES (%(accession)s, %(key)s, %(title)s, %(heading)s, %(source)s, %(text)s, %(text_sha256)s,
            %(pointer_kind)s)
    ON CONFLICT (accession, key) DO UPDATE SET
        title = EXCLUDED.title,
        heading = EXCLUDED.heading,
        source = EXCLUDED.source,
        text = EXCLUDED.text,
        text_sha256 = EXCLUDED.text_sha256,
        pointer_kind = EXCLUDED.pointer_kind
    WHERE (sections.title, sections.heading, sections.source, sections.text_sha256, sections.pointer_kind)
          IS DISTINCT FROM
          (EXCLUDED.title, EXCLUDED.heading, EXCLUDED.source, EXCLUDED.text_sha256, EXCLUDED.pointer_kind)
"""

# Sections that a new parse no longer produces. If one still has extractions or
# citations, the RESTRICT foreign keys make this fail loudly instead of losing them.
DELETE_STALE_SECTIONS = """
    DELETE FROM sections WHERE accession = %s AND NOT (key = ANY(%s))
"""


def load_rows(conn: psycopg.Connection, rows: dict) -> dict:
    """Write one filing's rows in one transaction. Returns how many rows changed."""
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(UPSERT_COMPANY, rows["company"])
            cur.execute(INSERT_TICKER, rows["ticker"])
            cur.execute(UPSERT_FILING, rows["filing"])
            filing_changed = cur.rowcount
            sections_changed = 0
            for section in rows["sections"]:
                cur.execute(UPSERT_SECTION, section)
                sections_changed += cur.rowcount
            keys = [s["key"] for s in rows["sections"]]
            cur.execute(DELETE_STALE_SECTIONS, (rows["filing"]["accession"], keys))
            sections_deleted = cur.rowcount
    return {"filings_changed": filing_changed, "sections_changed": sections_changed,
            "sections_deleted": sections_deleted}


def load_jsonl(conn: psycopg.Connection, path: Path = DEFAULT_JSONL, progress: bool = False) -> dict:
    """Load every record of a filings.jsonl file. Safe to run again: unchanged rows are not rewritten."""
    totals = {"records": 0, "filings_changed": 0, "sections_changed": 0, "sections_deleted": 0}
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            changed = load_rows(conn, record_to_rows(json.loads(line)))
            totals["records"] += 1
            for name, n in changed.items():
                totals[name] += n
            if progress and totals["records"] % 100 == 0:
                print(f"  {totals['records']} records...", flush=True)
    return totals


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the schema and load filings into Postgres.")
    parser.add_argument("--check", action="store_true", help="connect and print the server version")
    parser.add_argument("--create-schema", action="store_true", help="run schema.sql")
    parser.add_argument("--load", nargs="?", const=DEFAULT_JSONL, type=Path, metavar="JSONL",
                        help="load a filings.jsonl file (default data/filings.jsonl)")
    args = parser.parse_args(argv)
    if not (args.check or args.create_schema or args.load):
        parser.error("nothing to do: pass --check, --create-schema and/or --load")

    with connect() as conn:
        if args.check:
            print(conn.execute("SELECT version()").fetchone()[0])
        if args.create_schema:
            create_schema(conn)
            print("schema created (or already there)")
        if args.load:
            totals = load_jsonl(conn, args.load, progress=True)
            print(", ".join(f"{name}: {n}" for name, n in totals.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())

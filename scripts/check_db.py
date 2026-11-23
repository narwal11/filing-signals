"""Check the Postgres database against data/filings.jsonl (live: connects to the real database).

What it does: counts companies, filings, sections and characters in the database and compares
each with the number computed from the JSONL file (not typed in, so the check stays correct
when the file grows). It also confirms every raw timestamp equals its parsed one, re-hashes
every section's text in SQL, checks that row-level security is on for every table, and prints
table sizes.

When to use it: after `python db.py --load`, to prove the load is complete and correct.
Add --reload to prove that loading a second time changes nothing, or --safety-test to prove
the foreign keys and stale-section deletion work (done inside a transaction that is rolled
back, so nothing is kept). Needs DATABASE_URL in .env.

Example:
    python scripts/check_db.py
    python scripts/check_db.py --reload
    python scripts/check_db.py --safety-test
"""

import argparse
import json
import sys
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import classify  # noqa: E402
import db  # noqa: E402

TABLES = ["companies", "tickers", "filings", "sections", "extractions", "citations", "labels"]


def expected_from_jsonl(path: Path) -> dict:
    ciks, filings, sections, chars, non_standard, mismatches, raw = set(), 0, 0, 0, 0, 0, 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            filings += 1
            ciks.add(record["filing"]["cik"])
            sections += len(record["sections"])
            chars += sum(len(s["text"]) for s in record["sections"])
            raw += record["filing"].get("accepted_at_raw") is not None
            result = classify.check_filing(record)
            non_standard += bool(result and result["outcome"] == classify.NON_STANDARD)
            mismatches += bool(result and result["mismatch"])
    return {
        "companies": len(ciks), "filings": filings, "sections": sections, "sum(char_count)": chars,
        "layout = non_standard": non_standard, "sec_metadata_mismatch": mismatches,
        "accepted_at_raw filled": raw,
    }


def actual_from_db(conn) -> dict:
    one = lambda query: conn.execute(query).fetchone()[0]  # noqa: E731
    return {
        "companies": one("SELECT count(*) FROM companies"),
        "filings": one("SELECT count(*) FROM filings"),
        "sections": one("SELECT count(*) FROM sections"),
        "sum(char_count)": one("SELECT coalesce(sum(char_count), 0) FROM sections"),
        "layout = non_standard": one("SELECT count(*) FROM filings WHERE layout = 'non_standard'"),
        "sec_metadata_mismatch": one("SELECT count(*) FROM filings WHERE sec_metadata_mismatch"),
        "accepted_at_raw filled": one("SELECT count(*) FROM filings WHERE accepted_at_raw IS NOT NULL"),
    }


def snapshot(conn) -> dict:
    """Everything a reload must leave unchanged."""
    return {
        "counts": {
            t: conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t))).fetchone()[0]
            for t in TABLES
        },
        "sum(char_count)": conn.execute("SELECT coalesce(sum(char_count), 0) FROM sections").fetchone()[0],
        "section ids": dict(
            ((accession, key), id_) for id_, accession, key in conn.execute("SELECT id, accession, key FROM sections")
        ),
        "hashes": dict(conn.execute("SELECT id, text_sha256 FROM sections").fetchall()),
    }


def report(rows: list[tuple[str, object, object]]) -> bool:
    ok = True
    print(f"  {'check':<26} {'expected':>12} {'database':>12}")
    for name, want, got in rows:
        status = "ok" if want == got else "FAIL"
        ok &= want == got
        print(f"  {name:<26} {want:>12,} {got:>12,}  {status}")
    return ok


def first_record_with_sections(path: Path, minimum: int = 2) -> dict:
    """The first JSONL record that has at least `minimum` sections (so one can be removed)."""
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                record = json.loads(line)
                if len(record["sections"]) >= minimum:
                    return record
    raise SystemExit(f"no record with {minimum}+ sections in {path}")


def safety_test(conn, path: Path) -> bool:
    """Try two things that must be blocked or handled, inside ONE transaction that is rolled back.

    1. RESTRICT: a filing that has an extraction cannot be deleted.
    2. Stale sections: reloading a filing without one of its section keys deletes that section.
    Nothing persists: the whole block ends with psycopg.Rollback.
    """
    record = first_record_with_sections(path)
    accession = record["filing"]["accession"]
    print(f"Safety test on {record['filing']['ticker']} {record['filing']['form']} {accession} (rolled back at the end)")
    before = snapshot(conn)
    results = []

    with conn.transaction():  # autocommit connection: this is a real BEGIN ... and Rollback undoes all of it
        # (1) Insert a dummy extraction for the whole filing, then try to delete the filing.
        conn.execute(
            "INSERT INTO extractions (accession, model, prompt_version, output, input_text_sha256,"
            " input_tokens, output_tokens, cost_usd, latency_ms)"
            " VALUES (%s, 'safety-test', 'v0', '{}'::jsonb, '\\x00', 0, 0, 0, 0)",
            (accession,),
        )
        blocked = False
        try:
            with conn.transaction():  # a savepoint: the failed DELETE must not abort the outer transaction
                conn.execute("DELETE FROM filings WHERE accession = %s", (accession,))
        except psycopg.errors.ForeignKeyViolation as error:
            blocked = True
            print(f"  delete refused by Postgres: {error.diag.message_primary}")
        still_there = conn.execute("SELECT count(*) FROM filings WHERE accession = %s", (accession,)).fetchone()[0]
        results.append(("1. RESTRICT blocks deleting a filing with an extraction", blocked and still_there == 1))

        # (2) Reload the filing with one section removed: the loader must delete exactly that one.
        removed = record["sections"][-1]["key"]
        trimmed = {**record, "sections": record["sections"][:-1]}
        sections_before = conn.execute("SELECT count(*) FROM sections WHERE accession = %s", (accession,)).fetchone()[0]
        changed = db.load_rows(conn, db.record_to_rows(trimmed))
        keys_after = [k for (k,) in conn.execute("SELECT key FROM sections WHERE accession = %s", (accession,))]
        print(f"  removed section {removed!r}: {sections_before} -> {len(keys_after)} sections, loader reported {changed}")
        results.append((
            "2. loader deletes a section no longer in the parse",
            changed["sections_deleted"] == 1 and removed not in keys_after and len(keys_after) == sections_before - 1,
        ))

        raise psycopg.Rollback()  # undo everything above; transaction() catches it, so no error escapes

    after = snapshot(conn)
    results.append(("3. rollback left the database unchanged", before == after))
    for name, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    return all(ok for _, ok in results)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the Postgres load against filings.jsonl.")
    parser.add_argument("jsonl", nargs="?", type=Path, default=db.DEFAULT_JSONL)
    parser.add_argument("--reload", action="store_true", help="run load_jsonl again and confirm nothing changed")
    parser.add_argument("--safety-test", action="store_true",
                        help="check RESTRICT and stale-section deletion inside a transaction that is rolled back")
    args = parser.parse_args()

    if args.safety_test:
        with db.connect() as conn:
            ok = safety_test(conn, args.jsonl)
        print("\nSAFETY TEST PASSED" if ok else "\nSAFETY TEST FAILED")
        return 0 if ok else 1

    expected = expected_from_jsonl(args.jsonl)
    all_ok = True
    with db.connect() as conn:
        print("Database vs JSONL:")
        actual = actual_from_db(conn)
        all_ok &= report([(name, expected[name], actual[name]) for name in expected])

        # The raw SEC string and the stored timestamptz must be the same instant.
        bad_times = conn.execute(
            "SELECT count(*) FROM filings WHERE accepted_at_raw IS NOT NULL AND accepted_at_raw::timestamptz <> accepted_at"
        ).fetchone()[0]
        # db.py hashes the text in Python; re-hash it in SQL to prove the two agree.
        bad_hashes = conn.execute(
            "SELECT count(*) FROM sections WHERE text_sha256 <> sha256(convert_to(text, 'UTF8'))"
        ).fetchone()[0]
        all_ok &= report([("raw != accepted_at", 0, bad_times), ("wrong text_sha256", 0, bad_hashes)])

        no_rls = [t for (t,) in conn.execute(
            "SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'"
            " AND NOT relrowsecurity ORDER BY relname"
        )]
        print(f"  tables without row-level security: {no_rls or 'none'}")
        all_ok &= not no_rls

        if args.reload:
            print("\nIdempotency: running load_jsonl a second time...")
            before = snapshot(conn)
            changed = db.load_jsonl(conn, args.jsonl)
            after = snapshot(conn)
            print("  second load: " + ", ".join(f"{name}: {n}" for name, n in changed.items()))
            rows = [(f"{t} rows", before["counts"][t], after["counts"][t]) for t in TABLES]
            rows.append(("sum(char_count)", before["sum(char_count)"], after["sum(char_count)"]))
            rows += [(name, 0, changed[name]) for name in ("filings_changed", "sections_changed", "sections_deleted")]
            all_ok &= report(rows)
            ids_same = before["section ids"] == after["section ids"]
            hashes_same = before["hashes"] == after["hashes"]
            print(f"  section ids unchanged: {ids_same} ({len(after['section ids']):,} sections)")
            print(f"  section text hashes unchanged: {hashes_same}")
            all_ok &= ids_same and hashes_same

        print("\nSize:")
        print(f"  database: {conn.execute('SELECT pg_size_pretty(pg_database_size(current_database()))').fetchone()[0]}"
              " (Supabase free tier: 500 MB)")
        for table in TABLES:
            size = conn.execute("SELECT pg_size_pretty(pg_total_relation_size(%s::regclass))", (table,)).fetchone()[0]
            print(f"  {table:<12} {size}")

    print("\nALL CHECKS PASSED" if all_ok else "\nSOME CHECKS FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())

"""One-time backfill: add the exact SEC acceptanceDateTime string to every filing in filings.jsonl.

Records ingested before edgar.Filing kept `accepted_at_raw` only have the parsed `accepted_at`.
This fetches each company's submissions JSON once (about one request per company, through
EdgarClient, so the rate limit and User-Agent apply), writes `accepted_at_raw` into each record,
and checks that the raw string parses to the same instant as the stored `accepted_at`.

Usage:
    python scripts/backfill_accepted_raw.py [data/filings.jsonl]

The original file is kept as <name>.bak. Running it again is harmless.
"""

import argparse
import json
import os
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from edgar import EdgarClient  # noqa: E402

FORMS = ["10-K", "10-Q", "8-K"]


def read_records(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def fetch_raw_strings(client: EdgarClient, records: list[dict]) -> dict[str, str]:
    """Return {accession: raw acceptanceDateTime} for every company in `records`."""
    # ticker -> (cik, earliest filed_date) as stored in the file
    companies = {}
    for record in records:
        f = record["filing"]
        cik, earliest = companies.get(f["ticker"], (f["cik"], f["filed_date"]))
        companies[f["ticker"]] = (cik, min(earliest, f["filed_date"]))

    raw = {}
    for ticker, (cik, earliest) in sorted(companies.items()):
        # `since` skips older submission pages we don't need.
        filings = client.list_filings(ticker, FORMS, since=date.fromisoformat(earliest))
        for filing in filings:
            if filing.cik != cik:  # the ticker now points to a different company
                raise SystemExit(f"{ticker}: SEC says CIK {filing.cik}, file says {cik}")
            raw[filing.accession] = filing.accepted_at_raw
    return raw


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("jsonl", nargs="?", type=Path, default=ROOT / "data" / "filings.jsonl")
    args = parser.parse_args()

    records = read_records(args.jsonl)
    with EdgarClient(cache_dir=ROOT / "data" / "cache") as client:
        raw = fetch_raw_strings(client, records)

    filled, unchanged, not_found, mismatches = 0, 0, [], []
    for record in records:
        f = record["filing"]
        value = raw.get(f["accession"])
        if value is None:
            not_found.append(f["accession"])
            continue
        # Same instant? fromisoformat reads both "...Z" and "...+00:00" as UTC.
        if datetime.fromisoformat(value) != datetime.fromisoformat(f["accepted_at"]):
            mismatches.append((f["ticker"], f["accession"], f["accepted_at"], value))
        if f.get("accepted_at_raw") == value:
            unchanged += 1
        else:
            f["accepted_at_raw"] = value
            filled += 1

    # Write to a temp file, then swap it in, so a crash can't leave a half-written file.
    backup = args.jsonl.with_name(args.jsonl.name + ".bak")
    shutil.copy2(args.jsonl, backup)
    tmp = args.jsonl.with_name(args.jsonl.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as out:
        for record in records:
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.replace(tmp, args.jsonl)

    total = len(records)
    print(f"records: {total}")
    print(f"accepted_at_raw filled: {filled}/{total}  (already set: {unchanged}, not found at SEC: {len(not_found)})")
    for accession in not_found:
        print(f"  not found: {accession}")
    print(f"raw vs stored accepted_at mismatches: {len(mismatches)}")
    for ticker, accession, stored, value in mismatches:
        print(f"  {ticker} {accession}: stored {stored}, SEC raw {value}")
    print(f"backup: {backup}")
    return 1 if not_found or mismatches else 0


if __name__ == "__main__":
    sys.exit(main())

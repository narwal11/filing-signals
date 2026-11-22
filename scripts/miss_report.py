"""Report how often expected sections are missing from the output of ingest.py.

Usage (from anywhere):
    python scripts/miss_report.py [data/filings.jsonl] [--top 15]

Per form: how many filings are fine, how many are missing a required section ("absent"),
how many have one that is shorter than a real section would be ("too short"), and how many
only point to text elsewhere ("non-standard layout", e.g. JPM). Then the optional sections,
SEC metadata mismatches, and the worst filings with URLs so you can open them.
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

# This file lives in scripts/, so add the repo root to the import path to find classify.py.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The rules for judging one filing live in classify.py, shared with db.py, so the
# report and the database can never disagree about which filings are non-standard.
from classify import (  # noqa: E402
    FORMS, LAYOUT_MEANING, NON_STANDARD, OK, OPTIONAL, check_filing,
)

EXAMPLE_CHARS = 130


def layout_groups(non_standard: list[tuple[dict, dict]]) -> list[dict]:
    """Group non-standard filings by (ticker, form): how many, which sections, which kinds of pointer."""
    groups = {}
    for record, result in non_standard:
        filing = record["filing"]
        group = groups.setdefault(
            (filing["ticker"], filing["form"]),
            {"ticker": filing["ticker"], "form": filing["form"], "filings": 0, "keys": set(),
             "kinds": Counter(), "example_date": "", "example_text": ""},
        )
        group["filings"] += 1
        group["keys"].update(result["pointers"])
        group["kinds"].update({kind for kind, _ in result["pointers"].values()})  # once per filing
        if filing["filed_date"] > group["example_date"]:  # show the latest filing as the example
            first_key = sorted(result["pointers"])[0]
            group["example_date"] = filing["filed_date"]
            group["example_text"] = " ".join(result["pointers"][first_key][1].split())[:EXAMPLE_CHARS]
    return sorted(groups.values(), key=lambda g: (g["ticker"], g["form"]))


def percent(count: int, total: int) -> str:
    return f"{100 * count / total:.1f}%" if total else "n/a"


def describe(result: dict) -> str:
    parts = []
    if result["absent"]:
        parts.append("absent: " + ", ".join(result["absent"]))
    if result["too_short"]:
        parts.append("too short: " + ", ".join(f"{k} ({n:,} chars)" for k, n in result["too_short"].items()))
    return "; ".join(parts) or result["outcome"]


def main():
    parser = argparse.ArgumentParser(description="Report missing sections per form.")
    parser.add_argument("jsonl", nargs="?", type=Path, default=ROOT / "data" / "filings.jsonl")
    parser.add_argument("--top", type=int, default=15, help="how many worst filings to list")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    totals = defaultdict(Counter)  # form -> counts of: filings, ok, absent, too short, non-standard
    optional = defaultdict(Counter)  # (form, key) -> counts of: checked, absent
    mismatches = []  # (record, result)
    offenders = []  # (record, result)
    non_standard = []  # (record, result)
    bad_lines = 0
    with args.jsonl.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                result = check_filing(record)
            except (json.JSONDecodeError, KeyError):
                bad_lines += 1
                continue
            if result is None:
                continue
            form = record["filing"]["form"]
            counts = totals[form]
            counts["filings"] += 1
            if result["outcome"] == OK:
                counts["ok"] += 1
            elif result["outcome"] == NON_STANDARD:
                counts["non-standard"] += 1
                non_standard.append((record, result))
            else:
                counts["absent" if result["absent"] else "too short"] += 1
                offenders.append((record, result))
            for key in OPTIONAL.get(form, []):
                optional[form, key]["checked"] += 1
                optional[form, key]["absent"] += key in result["optional_absent"]
            if result["mismatch"]:
                mismatches.append((record, result))

    print(f"{'form':<6} {'filings':>8} {'ok':>6} {'absent':>7} {'too short':>10} {'non-standard':>13} {'miss rate':>10}")
    for form in FORMS:
        c = totals[form]
        misses = c["absent"] + c["too short"]
        print(
            f"{form:<6} {c['filings']:>8} {c['ok']:>6} {c['absent']:>7} {c['too short']:>10} "
            f"{c['non-standard']:>13} {percent(misses, c['filings']):>10}"
        )
    print("(miss rate = absent + too short, as a share of all filings of that form)")
    if bad_lines:
        print(f"({bad_lines} unreadable lines ignored)")

    if non_standard:
        print(f"\nNon-standard layouts: {len(non_standard)} filings whose text is elsewhere (we only have a pointer):")
        for group in layout_groups(non_standard):
            layout = "; ".join(f"{LAYOUT_MEANING[kind]} ({n})" for kind, n in group["kinds"].most_common())
            print(f"  {group['ticker']:<6} {group['form']:<5} {group['filings']:>3} filings   sections: {', '.join(sorted(group['keys']))}")
            print(f"         layout: {layout}")
            print(f"         e.g. {group['example_date']}: \"{group['example_text']}\"")

    print("\nOptional sections (not counted as misses):")
    for (form, key), c in sorted(optional.items()):
        print(f"  {form} {key}: absent in {c['absent']} of {c['checked']} filings ({percent(c['absent'], c['checked'])})")

    print(f"\nSEC metadata mismatches: {len(mismatches)} earnings 8-K(s) list Item 2.02 but the document has no 2.02 heading")
    for record, _ in mismatches:
        filing = record["filing"]
        print(f"  {filing['ticker']:<6} {filing['filed_date']}  headings in document: {', '.join(record['item_keys_found'])}")
        print(f"         {filing['primary_url']}")

    # Worst first: most problems, then oldest.
    offenders.sort(key=lambda item: (-(len(item[1]["absent"]) + len(item[1]["too_short"])), item[0]["filing"]["filed_date"]))
    print(f"\nWorst offenders (top {args.top} of {len(offenders)}):")
    for record, result in offenders[: args.top]:
        filing = record["filing"]
        print(f"  {filing['ticker']:<6} {filing['form']:<5} {filing['filed_date']}  {describe(result)}")
        print(f"         {filing['primary_url']}")


if __name__ == "__main__":
    main()

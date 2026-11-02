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

# This file lives in scripts/, so add the repo root to the import path to find parse.py.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from parse import Section, find_cross_references  # noqa: E402

# form -> {required section key: minimum characters for it to count as a real section}.
REQUIRED = {
    "10-K": {"1A": 5_000, "7": 5_000},
    "10-Q": {"I-2": 2_000},
}
# form -> optional section keys: reported in their own table, never counted as a miss.
# 10-K 7A: some filers fold market risk into MD&A (MCD). 10-Q II-1A: may be left out (XOM).
OPTIONAL = {
    "10-K": ["7A"],
    "10-Q": ["II-1A"],
}
# 8-K: only earnings 8-Ks (SEC metadata lists Item 2.02) are checked. One of these counts as
# evidence that the earnings text was captured: an EX-99.* exhibit, or an Item 2.02/8.01 body,
# with at least this many characters.
EIGHT_K_MIN_CHARS = 1_000
EIGHT_K_BODY_KEYS = ("2.02", "8.01")

FORMS = ["10-K", "10-Q", "8-K"]
# Outcomes for one filing:
OK, MISS, NON_STANDARD = "ok", "miss", "non-standard layout"


def check_filing(record: dict) -> dict | None:
    """Judge one record. Returns None if this filing isn't checked, else a dict with
    outcome, absent (keys), too_short ({key: chars}), optional_absent (keys), mismatch (bool)."""
    filing = record["filing"]
    form = filing["form"]
    if form not in FORMS:
        return None
    sections = {s["key"]: s for s in record["sections"]}
    result = {"outcome": OK, "absent": [], "too_short": {}, "optional_absent": [], "mismatch": False}

    if form == "8-K":
        if "2.02" not in filing["items"]:
            return None  # not an earnings 8-K
        candidates = [s for s in record["sections"] if s["source"].startswith("EX-99") or s["key"] in EIGHT_K_BODY_KEYS]
        if not any(s["char_count"] >= EIGHT_K_MIN_CHARS for s in candidates):
            if candidates:
                result["too_short"] = {s["key"]: s["char_count"] for s in candidates}
            else:
                result["absent"] = ["EX-99 or Item 2.02/8.01 text"]
        # The SEC says Item 2.02, but the document has item headings and none is 2.02.
        found = record.get("item_keys_found")
        result["mismatch"] = bool(found) and "2.02" not in found
    else:
        for key, min_chars in REQUIRED[form].items():
            if key not in sections:
                result["absent"].append(key)
            elif sections[key]["char_count"] < min_chars:
                result["too_short"][key] = sections[key]["char_count"]
        result["optional_absent"] = [key for key in OPTIONAL[form] if key not in sections]

    if result["absent"] or result["too_short"]:
        result["outcome"] = MISS
        # A short section that only says "see pages 46-160" is a layout, not a parser miss.
        if result["too_short"] and not result["absent"] and form != "8-K":
            as_sections = [Section(s["key"], s["title"], s["text"], s["source"]) for s in record["sections"]]
            pointers = set(find_cross_references(as_sections))
            if set(result["too_short"]) <= pointers:
                result["outcome"] = NON_STANDARD
    return result


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

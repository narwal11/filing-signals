"""Compare two ingest outputs filing by filing, to see exactly what a parser change did.

Usage (from anywhere):
    python scripts/compare_runs.py data/filings_old.jsonl data/filings.jsonl

Matches filings by accession number and reports:
  - filings whose set of sections changed (keys added or removed), with the likely reason,
  - filings where a section's length changed by more than 5%,
  - how many filings were identical or only changed slightly (footers, whitespace).
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

SMALL_CHANGE = 0.05  # up to 5% shorter or longer counts as "cleanup only"


def load(path: Path) -> dict:
    """accession -> (ticker, form, filed date, url, {section key: char count}, number of item headings found)."""
    filings = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            record = json.loads(line)
            filing = record["filing"]
            sizes = {s["key"]: s["char_count"] for s in record["sections"]}
            found = record.get("item_keys_found")  # only in newer files
            filings[filing["accession"]] = (
                filing["ticker"], filing["form"], filing["filed_date"], filing["primary_url"], sizes,
                None if found is None else len(found),
            )
    return filings


# A 10-K split by its "Item N" headings finds 20+ distinct items; one split by plain
# titles (the annual-report fallback) finds about ten.
MANY_ITEM_HEADINGS = 15


def likely_reason(form: str, items_found_new, added: list) -> str:
    """A best guess of which parser change caused a key difference. Inferred, not recorded."""
    if form == "10-K" and items_found_new is not None:
        if items_found_new >= MANY_ITEM_HEADINGS:
            return "Item heading now accepted (long heading with a variant title)"
        return "title-heading fallback (the filing has no usable Item headings)"
    if added:
        return "section found that was missed before"
    return "section no longer returned (check this one by hand)"


def main():
    parser = argparse.ArgumentParser(description="Compare two ingest outputs.")
    parser.add_argument("old", type=Path)
    parser.add_argument("new", type=Path)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    old, new = load(args.old), load(args.new)
    both = sorted(set(old) & set(new), key=lambda a: (old[a][0], old[a][2]))
    print(f"old: {len(old)} filings, new: {len(new)} filings, in both: {len(both)}")
    print(f"only in old: {len(set(old) - set(new))}, only in new: {len(set(new) - set(old))}")

    counts = Counter()  # (form, outcome) -> filings
    key_changes = []  # filings whose set of sections changed
    size_changes = []  # filings with a section more than 5% different
    for accession in both:
        ticker, form, filed, url, old_sizes, _ = old[accession]
        new_sizes, items_found_new = new[accession][4], new[accession][5]
        added = sorted(set(new_sizes) - set(old_sizes))
        removed = sorted(set(old_sizes) - set(new_sizes))
        big = {
            key: (old_sizes[key], new_sizes[key])
            for key in set(old_sizes) & set(new_sizes)
            if abs(new_sizes[key] - old_sizes[key]) > SMALL_CHANGE * max(old_sizes[key], 1)
        }
        if added or removed:
            counts[form, "sections added/removed"] += 1
            key_changes.append((ticker, form, filed, url, added, removed, likely_reason(form, items_found_new, added)))
        elif big:
            counts[form, "a section changed by more than 5%"] += 1
            size_changes.append((ticker, form, filed, url, big))
        elif old_sizes != new_sizes:
            counts[form, "cleanup only (under 5%)"] += 1
        else:
            counts[form, "identical"] += 1

    print("\nBy form:")
    for form in ("10-K", "10-Q", "8-K"):
        parts = [f"{n} {what}" for (f, what), n in sorted(counts.items()) if f == form]
        print(f"  {form}: " + "; ".join(parts))

    print(f"\nFilings whose sections were added or removed ({len(key_changes)}):")
    grouped = defaultdict(list)
    for ticker, form, filed, url, added, removed, reason in key_changes:
        grouped[ticker, form, tuple(added), tuple(removed), reason].append(filed)
    for (ticker, form, added, removed, reason), dates in sorted(grouped.items()):
        change = []
        if added:
            change.append("added " + ", ".join(added))
        if removed:
            change.append("removed " + ", ".join(removed))
        print(f"  {ticker} {form} x{len(dates)} ({dates[0]} .. {dates[-1]}): {'; '.join(change)}")
        print(f"      why: {reason}")

    print(f"\nFilings where a kept section changed by more than 5% ({len(size_changes)}):")
    for ticker, form, filed, url, big in size_changes:
        detail = ", ".join(f"{k} {a:,} -> {b:,}" for k, (a, b) in sorted(big.items()))
        print(f"  {ticker:<6} {form:<5} {filed}  {detail}")
        print(f"         {url}")


if __name__ == "__main__":
    main()

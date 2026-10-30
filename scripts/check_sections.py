"""Sanity-check section extraction on real filings.

Usage (from anywhere):
    python scripts/check_sections.py

For each ticker, takes the latest 10-K, the latest 10-Q, and the latest earnings 8-K
(one with Item 2.02), runs them through ingest.process, and prints one row per kept section.
Flags filings where an expected section is missing or suspiciously short.
This hits the real SEC, which is why it is a script and not a test.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

# This file lives in scripts/, so add the repo root to the import path to find edgar.py.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from edgar import EdgarClient  # noqa: E402
from ingest import process  # noqa: E402
from parse import Section, find_cross_references  # noqa: E402

TICKERS = ["AAPL", "MSFT", "JPM", "XOM", "KO"]
FORMS = ["10-K", "10-Q", "8-K"]
LOOKBACK_DAYS = 400  # long enough to include the latest 10-K
PREVIEW_CHARS = 80

# form -> {section key: minimum chars}. 0 means "must be present, any length":
# a 10-Q's II-1A is often just "no material changes since our 10-K".
REQUIRED = {
    "10-K": {"1A": 5_000, "7": 5_000},
    "10-Q": {"I-2": 5_000, "II-1A": 0},
    "8-K": {"2.02": 0},
}
MIN_PRESS_RELEASE_CHARS = 1_000  # 8-K: at least one EX-99 exhibit this long

# Each filing ends up in exactly one of these buckets.
OK, MISS, NON_STANDARD, NOT_CHECKED = "ok", "miss", "non-standard layout", "not checked"


def find_problems(form: str, sections: list[Section]) -> list[str]:
    """Return what is missing or too short for this form (empty list = fine)."""
    problems = []
    by_key = {s.key: s for s in sections}
    for key, min_chars in REQUIRED[form].items():
        if key not in by_key:
            problems.append(f"{key} missing")
        elif by_key[key].char_count < min_chars:
            problems.append(f"{key} only {by_key[key].char_count:,} chars")
    if form == "8-K":
        exhibits = [s for s in sections if s.source.startswith("EX-99")]
        if not any(s.char_count >= MIN_PRESS_RELEASE_CHARS for s in exhibits):
            problems.append(f"no EX-99 exhibit with at least {MIN_PRESS_RELEASE_CHARS:,} chars")
    return problems


def check_filing(edgar: EdgarClient, ticker: str, form: str) -> tuple[str, list[str]]:
    """Print the section rows for one ticker's latest filing of `form`. Return (bucket, notes)."""
    since = date.today() - timedelta(days=LOOKBACK_DAYS)
    filings = edgar.list_filings(ticker, [form], since=since)
    if form == "8-K":
        filings = [f for f in filings if "2.02" in f.items]  # earnings releases only
    if not filings:
        return NOT_CHECKED, [f"no {form} filed since {since}"]
    latest = filings[-1]
    where = f"filed {latest.filed_date}: {latest.primary_url}"

    sections = process(edgar, latest)
    for s in sections:
        preview = " ".join(s.text[:PREVIEW_CHARS].split())  # newlines -> spaces
        print(f"{ticker:<6} | {form:<4} | {s.key:<7} | {s.char_count:>9,} | {preview}")

    # A cross-reference filing isn't a parser miss: the text is elsewhere by design.
    cross_refs = find_cross_references(sections)
    if cross_refs:
        return NON_STANDARD, [f"{', '.join(cross_refs)} point to other pages", where]

    problems = find_problems(form, sections)
    if problems:
        return MISS, problems + [where]
    return OK, []


def main():
    # The Windows console can't always print characters like bullets; don't crash on them.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print(f"{'ticker':<6} | {'form':<4} | {'key':<7} | {'chars':>9} | first {PREVIEW_CHARS} characters")
    print("-" * 120)

    results = {}  # (ticker, form) -> (bucket, notes)
    with EdgarClient(cache_dir=ROOT / "data" / "cache") as edgar:
        for ticker in TICKERS:
            for form in FORMS:
                try:
                    results[ticker, form] = check_filing(edgar, ticker, form)
                except Exception as error:  # keep checking the other filings
                    results[ticker, form] = (NOT_CHECKED, [f"error: {error!r}"])
            print("-" * 120)

    for bucket in (MISS, NON_STANDARD, NOT_CHECKED):
        found = [(t, f) for (t, f), (b, _) in results.items() if b == bucket]
        if found:
            print(f"{bucket.upper()}:")
            for ticker, form in found:
                for note in results[ticker, form][1]:
                    print(f"  {ticker} {form}: {note}")

    # The miss rate only counts standard-layout filings; the rest are reported separately.
    print("\nSUMMARY:")
    any_failure = False
    for form in FORMS:
        buckets = [b for (t, f), (b, _) in results.items() if f == form]
        misses, oks = buckets.count(MISS), buckets.count(OK)
        standard = misses + oks
        rate = f"{misses / standard:.0%}" if standard else "n/a"
        print(
            f"  {form:<4}: miss rate {misses}/{standard} standard filings ({rate}); "
            f"{buckets.count(NON_STANDARD)} non-standard layout; {buckets.count(NOT_CHECKED)} not checked"
        )
        any_failure = any_failure or misses > 0 or NOT_CHECKED in buckets
    if any_failure:
        sys.exit(1)  # non-zero exit code, so a failing check is visible to other tools


if __name__ == "__main__":
    main()

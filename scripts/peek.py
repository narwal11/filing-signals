"""Look at how a filing converts to text.

Usage (from anywhere):
    python scripts/peek.py AAPL 10-K

Downloads the latest filing of that form, saves the text to data/peek/<ticker>-<form>.txt
and prints the first 80 lines. This hits the real SEC, which is why it is a script and not a test.
"""

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

# This file lives in scripts/, so add the repo root to the import path to find edgar.py.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from edgar import EdgarClient  # noqa: E402
from parse import html_to_text  # noqa: E402

PREVIEW_LINES = 80


def main():
    parser = argparse.ArgumentParser(description="Convert a company's latest filing to text.")
    parser.add_argument("ticker", help="e.g. AAPL")
    parser.add_argument("form", help="e.g. 10-K, 10-Q, 8-K")
    args = parser.parse_args()
    ticker = args.ticker.upper()

    # The Windows console can't always print characters like bullets; don't crash on them.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    with EdgarClient(cache_dir=ROOT / "data" / "cache") as edgar:
        # Only look at roughly the last year, so the older submission pages are never downloaded.
        since = date.today() - timedelta(days=400)
        filings = edgar.list_filings(ticker, [args.form], since=since)
        if not filings:
            sys.exit(f"No {args.form} filings found for {ticker} since {since}.")
        latest = filings[-1]
        print(f"Latest {latest.form}: filed {latest.filed_date}\n{latest.primary_url}\n")
        text = html_to_text(edgar.download(latest))

    out_path = ROOT / "data" / "peek" / f"{ticker}-{args.form}.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")

    lines = text.split("\n")
    print("\n".join(lines[:PREVIEW_LINES]))
    print(f"\n--- showing {min(PREVIEW_LINES, len(lines))} of {len(lines)} lines; saved to {out_path} ---")


if __name__ == "__main__":
    main()

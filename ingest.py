"""Download filings, split them into sections, and append one JSON line per filing.

Usage:
    python ingest.py --tickers AAPL MSFT --forms 10-K 10-Q 8-K --since 2021-01-01
    python ingest.py --tickers-file tickers.txt --jsonl data/filings.jsonl -v

Safe to stop and re-run: accession numbers already in the output file are skipped.
"""

import argparse
import json
import logging
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from edgar import EdgarClient, Filing
import re

from parse import Section, html_to_text, split_sections, strip_page_noise

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("ingest")

# The sections Phase 1 keeps for each form. Everything else is dropped.
RELEVANT_KEYS = {
    "10-K": {"1A", "7", "7A"},
    "10-Q": {"I-2", "II-1A"},
    "8-K": {"2.02", "7.01", "8.01"},
}

# Exhibit files we can turn into text. Images, PDFs and XBRL files are skipped.
TEXT_EXTENSIONS = (".htm", ".html", ".txt")

# The SEC's packaging line at the top of an exhibit: type, sequence number, file name, type again.
#   "EX-99.1 2 a8-kex991q1202112262020.htm EX-99.1"
SEC_EXHIBIT_HEADER_RE = re.compile(r"[ \t]*EX-\d+(?:\.\d+)?[ \t]+\d+[ \t]+\S+\.(?:htm|html|txt)\b.*", re.IGNORECASE)


def _strip_exhibit_header(text: str) -> str:
    """Drop the SEC packaging line if it is the first line of the exhibit."""
    first, _, rest = text.partition("\n")
    return rest.lstrip("\n") if SEC_EXHIBIT_HEADER_RE.fullmatch(first) else text


def _exhibit_section(client: EdgarClient, doc_type: str, url: str) -> Section | None:
    """Download one exhibit and wrap it as a Section, or return None if it has no text."""
    raw = client.get(url)
    if url.lower().endswith(".txt"):
        # Plain text already has meaningful line breaks; html_to_text would turn them into spaces.
        text = raw.decode("utf-8", errors="replace").strip()
    else:
        text = html_to_text(raw)
    text = strip_page_noise(_strip_exhibit_header(text))
    if not text:
        return None
    return Section(key=doc_type, title=doc_type, text=text, source=doc_type)


def process_with_keys(client: EdgarClient, filing: Filing) -> tuple[list[Section], list[str]]:
    """Return (the sections we keep, the keys of ALL item sections found in the document).

    The second list lets a report tell "the document has no Item 2.02" (the SEC's metadata
    was wrong) from "we failed to find the Item 2.02 that is there".

    For earnings 8-Ks (Item 2.02), the numbers are in the press release attached as
    Exhibit 99.x, not in the 8-K itself, so those exhibits are added as extra sections.
    """
    text = html_to_text(client.download(filing))
    all_sections = split_sections(text, filing.form)
    sections = [s for s in all_sections if s.key in RELEVANT_KEYS[filing.form]]

    if filing.form == "8-K" and "2.02" in filing.items:
        for doc_type, url in client.exhibits(filing):
            if doc_type.startswith("EX-99") and url.lower().endswith(TEXT_EXTENSIONS):
                section = _exhibit_section(client, doc_type, url)
                if section is not None:
                    sections.append(section)
    return sections, [s.key for s in all_sections]


def process(client: EdgarClient, filing: Filing) -> list[Section]:
    """Turn one filing into the sections we keep."""
    return process_with_keys(client, filing)[0]


# ---- the command-line tool ------------------------------------------------


@dataclass
class Stats:
    ok: Counter = field(default_factory=Counter)  # form -> filings with at least one section
    failed: int = 0  # filings that raised an error (not written, so a re-run retries them)
    failed_tickers: int = 0  # tickers whose filing list could not be fetched
    no_sections: int = 0  # filings processed fine but no wanted section was found
    skipped: int = 0  # filings already in the output file


def read_tickers(cli_tickers: list[str], tickers_file: Path | None) -> list[str]:
    """Combine --tickers and --tickers-file into one upper-case list without duplicates."""
    tickers = list(cli_tickers)
    if tickers_file is not None:
        for line in tickers_file.read_text(encoding="utf-8").splitlines():
            tickers += line.split("#")[0].split()  # drop "# comments", then split on spaces
    return list(dict.fromkeys(t.upper() for t in tickers))  # dict keeps order, drops repeats


def load_done(path: Path) -> set[str]:
    """Return the accession numbers already written to the JSONL file."""
    done = set()
    if not path.exists():
        return done
    with path.open(encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                done.add(json.loads(line)["filing"]["accession"])
            except (json.JSONDecodeError, KeyError):
                log.warning("%s line %d is not a valid record; ignoring it", path, line_number)
    return done


def filing_to_dict(filing: Filing) -> dict:
    """A plain dict of the filing's fields that json.dumps can handle."""
    data = asdict(filing)
    data["filed_date"] = filing.filed_date.isoformat()
    data["report_date"] = filing.report_date.isoformat() if filing.report_date else None
    data["accepted_at"] = filing.accepted_at.isoformat() if filing.accepted_at else None
    data["items"] = list(filing.items)
    data["primary_url"] = filing.primary_url
    return data


def section_to_dict(section: Section) -> dict:
    data = asdict(section)
    data["char_count"] = section.char_count
    return data


def select_filings(
    client: EdgarClient,
    ticker: str,
    forms: list[str],
    since: date | None,
    earnings_only: bool,
    max_per_ticker: int | None,
) -> list[Filing]:
    """List the filings to process for one ticker, oldest first."""
    filings = client.list_filings(ticker, forms, since=since)
    if earnings_only:
        # Most 8-Ks are about other things (executive changes, deals); keep only Item 2.02.
        filings = [f for f in filings if f.form != "8-K" or "2.02" in f.items]
    if max_per_ticker:
        # The most recent N of EACH form, so "10-K" isn't crowded out by frequent 8-Ks.
        kept = []
        for form in forms:
            kept += [f for f in filings if f.form == form][-max_per_ticker:]
        filings = sorted(kept, key=lambda f: f.filed_date)
    return filings


def run(
    client: EdgarClient,
    tickers: list[str],
    forms: list[str],
    since: date | None,
    jsonl_path: Path,
    earnings_only: bool = True,
    max_per_ticker: int | None = None,
) -> Stats:
    """Process every filing and append one JSON line each. One failure never stops the run."""
    stats = Stats()
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    done = load_done(jsonl_path)
    log.info("%d filings already in %s", len(done), jsonl_path)

    # A crash mid-write can leave a last line with no newline; don't glue the next record onto it.
    needs_newline = jsonl_path.exists() and jsonl_path.stat().st_size > 0 and not jsonl_path.read_bytes().endswith(b"\n")

    with jsonl_path.open("a", encoding="utf-8") as out:
        if needs_newline:
            out.write("\n")

        for ticker in tickers:
            try:
                filings = select_filings(client, ticker, forms, since, earnings_only, max_per_ticker)
            except Exception as error:
                stats.failed_tickers += 1
                log.error("%s: could not list filings: %r", ticker, error, exc_info=log.isEnabledFor(logging.DEBUG))
                continue
            log.info("%s: %d filings to consider", ticker, len(filings))

            for filing in filings:
                label = f"{ticker} {filing.form} {filing.filed_date} {filing.accession}"
                if filing.accession in done:
                    stats.skipped += 1
                    log.debug("%s: already done, skipping", label)
                    continue
                try:
                    sections, item_keys_found = process_with_keys(client, filing)
                except Exception as error:
                    stats.failed += 1
                    log.error("%s: failed: %r", label, error, exc_info=log.isEnabledFor(logging.DEBUG))
                    continue

                if sections:
                    stats.ok[filing.form] += 1
                    log.info("%s: %s", label, ", ".join(f"{s.key} ({s.char_count:,})" for s in sections))
                else:
                    # Still written, so miss_report.py can see it (and a re-run won't redo it).
                    stats.no_sections += 1
                    log.warning("%s: no sections found", label)
                record = {
                    "filing": filing_to_dict(filing),
                    "sections": [section_to_dict(s) for s in sections],
                    "item_keys_found": item_keys_found,  # every item heading found, kept or not
                }
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                out.flush()  # if the run is interrupted, everything finished so far is on disk
                done.add(filing.accession)
    return stats


def print_summary(stats: Stats):
    ok = ", ".join(f"{form} {count}" for form, count in sorted(stats.ok.items())) or "none"
    print("\nSummary")
    print(f"  ok:          {ok}")
    print(f"  failed:      {stats.failed} filings, {stats.failed_tickers} tickers")
    print(f"  no_sections: {stats.no_sections}")
    print(f"  skipped:     {stats.skipped} (already in the output file)")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download SEC filings and split them into sections.")
    parser.add_argument("--tickers", nargs="*", default=[], help="tickers, e.g. AAPL MSFT")
    parser.add_argument("--tickers-file", type=Path, help="text file with one ticker per line; # starts a comment")
    parser.add_argument("--forms", nargs="+", default=list(RELEVANT_KEYS), choices=list(RELEVANT_KEYS))
    parser.add_argument("--since", type=date.fromisoformat, default=date(2021, 1, 1),
                        help="only filings on or after this date, YYYY-MM-DD (default: 2021-01-01)")
    parser.add_argument("--jsonl", type=Path, default=Path("data/filings.jsonl"), help="output file (appended to)")
    parser.add_argument("--max-per-ticker", type=int, metavar="N",
                        help="only the most recent N filings of each form per ticker (handy for trial runs)")
    parser.add_argument("--all-8ks", action="store_true", help="include 8-Ks without Item 2.02 (default: earnings only)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging, with tracebacks")
    args = parser.parse_args(argv)
    if not args.tickers and not args.tickers_file:
        parser.error("give at least one of --tickers or --tickers-file")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # it logs every single request at INFO

    try:
        tickers = read_tickers(args.tickers, args.tickers_file)
    except OSError as error:
        sys.exit(f"Cannot read the tickers file: {error}")
    log.info("%d tickers, forms %s, since %s", len(tickers), " ".join(args.forms), args.since)

    with EdgarClient(cache_dir=ROOT / "data" / "cache") as client:
        stats = run(
            client,
            tickers,
            args.forms,
            args.since,
            args.jsonl,
            earnings_only=not args.all_8ks,
            max_per_ticker=args.max_per_ticker,
        )
    print_summary(stats)
    return 1 if stats.failed or stats.failed_tickers else 0


if __name__ == "__main__":
    sys.exit(main())

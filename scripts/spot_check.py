"""Hand-check a random sample of extracted sections.

Usage (from anywhere):
    python scripts/spot_check.py            # write data/spot_check.md with 20 filings to review
    python scripts/spot_check.py --score    # read your checkmarks and print accuracy with a 95% interval

Open data/spot_check.md, compare each section's start and end with the filing at its URL, and
tick "- [x] correct" if the section starts where it should and ends where it should.
Leave the box empty if it is wrong. (Unticked boxes are counted as wrong.)
"""

import argparse
import json
import math
import random
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))  # so miss_report can be imported by tests too

from miss_report import OK, check_filing  # noqa: E402

SEED = 42  # fixed, so everyone gets the same 20 filings
QUOTAS = {"10-K": 7, "10-Q": 7, "8-K": 6}  # 20 filings in total
SNIPPET_CHARS = 200

# Sections to review per form (CLAUDE.md). For 8-Ks the second one is the press release.
EXPECTED = {
    "10-K": ["1A", "7", "7A"],
    "10-Q": ["I-2", "II-1A"],
    "8-K": ["2.02"],
}

# Lines in data/spot_check.md. Anything may follow "correct" (your notes), so there is no "$".
#   ## 3. UNH 10-K 2025-02-27      -> a filing: ticker, form, date
#   ### 1A: 65,809 chars           -> a section: key
#   - [x] correct | any note       -> its checkbox: "x" = correct, " " = wrong
FILING_HEADING_RE = re.compile(r"^## \d+\. (\S+) (\S+) (\S+)")
SECTION_HEADING_RE = re.compile(r"^### (\S+): [\d,]+ chars")
CHECKBOX_RE = re.compile(r"^- \[( |x|X)\] correct\b")
ANY_CHECKBOX_RE = re.compile(r"^- \[.*?\]")  # catches boxes CHECKBOX_RE rejects, like "- [~] correct"


def expected_sections(record: dict) -> list[dict] | None:
    """The sections to review for this filing, or None if any of them is missing."""
    form = record["filing"]["form"]
    by_key = {s["key"]: s for s in record["sections"]}
    wanted = [by_key.get(key) for key in EXPECTED[form]]
    if form == "8-K":
        # "Exhibit 99.1" is called EX-99, EX-99.1 or EX-99.01 depending on the filer: take the first.
        exhibits = [s for s in record["sections"] if s["source"].startswith("EX-99")]
        wanted.append(exhibits[0] if exhibits else None)
    return None if None in wanted else wanted


def reviewed_filings(markdown: str) -> set[tuple[str, str, str]]:
    """(ticker, form, date) of every filing listed in an earlier spot-check file."""
    return {m.groups() for m in map(FILING_HEADING_RE.match, markdown.splitlines()) if m}


def pick_filings(records: list[dict], seed: int = SEED, quotas: dict = QUOTAS, exclude: set = frozenset()) -> list[dict]:
    """Pick filings at random, a different company each, from those where everything was found.

    `exclude` holds (ticker, form, date) of filings already reviewed, so a new sample is fresh.
    """
    eligible = defaultdict(lambda: defaultdict(list))  # form -> ticker -> [records]
    for record in records:
        result = check_filing(record)
        filing = record["filing"]
        # Only filings the report calls fine AND that have every expected section
        # (the report allows e.g. a missing 10-K 7A; here we want all of them).
        if result and result["outcome"] == OK and expected_sections(record):
            if (filing["ticker"], filing["form"], filing["filed_date"]) not in exclude:
                eligible[filing["form"]][filing["ticker"]].append(record)

    rng = random.Random(seed)
    chosen, used_tickers = [], set()
    for form, quota in quotas.items():
        tickers = sorted(eligible[form])  # sorted first, so the shuffle is the same every run
        rng.shuffle(tickers)
        # Companies not picked yet come first; reuse one only if we run out.
        ordered = [t for t in tickers if t not in used_tickers] + [t for t in tickers if t in used_tickers]
        for ticker in ordered[:quota]:
            candidates = sorted(eligible[form][ticker], key=lambda r: r["filing"]["filed_date"])
            chosen.append(rng.choice(candidates))
            used_tickers.add(ticker)
    return chosen


def snippet_lines(text: str) -> list[str]:
    """The first and last 200 characters of a section (the whole text if it is that short)."""
    def flat(s):
        return " ".join(s.split())  # one line, so it reads well in the markdown preview

    if len(text) <= 2 * SNIPPET_CHARS:
        return [f"> **full text:** {flat(text)}"]
    return [
        f"> **first {SNIPPET_CHARS}:** {flat(text[:SNIPPET_CHARS])}",
        ">",
        f"> **last {SNIPPET_CHARS}:** {flat(text[-SNIPPET_CHARS:])}",
    ]


def render(records: list[dict]) -> str:
    lines = [
        "# Spot check",
        "",
        "For each section: open the URL, find the section, and compare. Tick the box if it **starts at its own",
        "heading's content and ends where the section really ends** (no text from the next item, no",
        "missing opening). Leave it empty if wrong. Then run `python scripts/spot_check.py --score`.",
        "",
    ]
    for number, record in enumerate(records, start=1):
        filing = record["filing"]
        lines += [f"## {number}. {filing['ticker']} {filing['form']} {filing['filed_date']}", "", filing["primary_url"], ""]
        for section in expected_sections(record):
            lines += [f"### {section['key']}: {section['char_count']:,} chars", ""]
            lines += snippet_lines(section["text"])
            lines += ["", "- [ ] correct", ""]
    return "\n".join(lines)


def wilson_interval(correct: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion.

    Why Wilson and not the textbook formula p +/- z*sqrt(p(1-p)/n)? With only a few dozen
    sections and accuracy near 100%, the textbook interval breaks: at 47/47 correct it has zero
    width ("100% +/- 0"), and at 45/47 its upper end can pass 100%. Wilson stays inside 0..1
    and gives a sensible, honest-looking range at small n and extreme p.
    """
    p = correct / total
    denominator = 1 + z**2 / total
    center = (p + z**2 / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z**2 / (4 * total**2)) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


@dataclass
class Review:
    ticker: str
    form: str
    key: str
    ticked: bool  # True = you ticked it ("correct"); False = left empty ("wrong")


def read_reviews(markdown: str) -> tuple[list[Review], list[str]]:
    """Pair every section header with the checkbox line under it.

    Driven by the "### key: N chars" headers, so a checkbox with a note after "correct"
    is counted like any other. Returns (reviews, errors); with errors, don't trust the reviews.
    """
    reviews, errors = [], []
    ticker = form = "?"
    waiting_for = None  # (key, line number) of a section header that has no checkbox yet
    headers = boxes = 0

    for number, line in enumerate(markdown.splitlines(), start=1):
        filing = FILING_HEADING_RE.match(line)
        section = SECTION_HEADING_RE.match(line)
        box = CHECKBOX_RE.match(line)

        if filing or section:
            if waiting_for:  # the previous section never got its checkbox line
                errors.append(f"line {waiting_for[1]}: section {ticker} {form} {waiting_for[0]} has no '- [ ] correct' line")
                waiting_for = None
        if filing:
            ticker, form = filing.group(1), filing.group(2)
        elif section:
            headers += 1
            waiting_for = (section.group(1), number)
        elif line.startswith("### "):
            errors.append(f"line {number}: section header not recognised (expected '### key: N chars'): {line[:60]}")
        elif ANY_CHECKBOX_RE.match(line):
            boxes += 1
            if not box:
                errors.append(f"line {number}: checkbox not recognised (use '- [ ] correct' or '- [x] correct'): {line[:60]}")
            elif waiting_for is None:
                errors.append(f"line {number}: a checkbox line with no section header above it (second box?)")
            else:
                reviews.append(Review(ticker, form, waiting_for[0], box.group(1) in ("x", "X")))
                waiting_for = None

    if waiting_for:
        errors.append(f"line {waiting_for[1]}: section {ticker} {form} {waiting_for[0]} has no '- [ ] correct' line")
    if not (headers == boxes == len(reviews)):
        errors.append(f"counts don't add up: {headers} section headers, {boxes} checkbox lines, {len(reviews)} paired")
    return reviews, errors


def score(path: Path, verbose: bool = False) -> int:
    reviews, errors = read_reviews(path.read_text(encoding="utf-8"))
    if errors or not reviews:
        print(f"ERROR: cannot score {path}:")
        for error in errors or ["no sections found"]:
            print(f"  - {error}")
        return 1

    if verbose:
        for review in reviews:
            print(f"{review.ticker} | {review.form} | {review.key} | {'correct' if review.ticked else 'wrong'}")
        print()
    correct = sum(r.ticked for r in reviews)
    low, high = wilson_interval(correct, len(reviews))
    print(f"Sections reviewed: {len(reviews)}   correct (ticked): {correct}   wrong (unticked): {len(reviews) - correct}")
    print(f"Accuracy: {100 * correct / len(reviews):.1f}% ({correct}/{len(reviews)})")
    print(f"95% Wilson interval: {100 * low:.1f}% to {100 * high:.1f}%")
    totals, right = Counter(r.form for r in reviews), Counter(r.form for r in reviews if r.ticked)
    print("By form: " + ", ".join(f"{form} {right[form]}/{n}" for form, n in sorted(totals.items())))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Hand-check a random sample of extracted sections.")
    parser.add_argument("--jsonl", type=Path, default=ROOT / "data" / "filings.jsonl")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "spot_check.md")
    parser.add_argument("--score", action="store_true", help="read the ticked boxes and print the accuracy")
    parser.add_argument("--force", action="store_true", help="overwrite an existing spot_check.md (loses your ticks!)")
    parser.add_argument("--verbose", action="store_true", help="with --score: list every section as correct/wrong")
    parser.add_argument("--seed", type=int, default=SEED, help=f"random seed for the sample (default {SEED})")
    parser.add_argument("--exclude", type=Path, nargs="*", default=[],
                        help="earlier spot-check files: their filings are not picked again")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.score:
        return score(args.out, args.verbose)

    if args.out.exists() and not args.force:
        sys.exit(f"{args.out} already exists and may hold your checkmarks. Use --score to read it, or --force to overwrite.")
    with args.jsonl.open(encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]
    exclude = set()
    for path in args.exclude:
        exclude |= reviewed_filings(path.read_text(encoding="utf-8"))
    chosen = pick_filings(records, seed=args.seed, exclude=exclude)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(chosen), encoding="utf-8")
    sections = sum(len(expected_sections(r)) for r in chosen)
    print(f"Wrote {args.out}: {len(chosen)} filings, {sections} sections to check (seed {args.seed}, "
          f"{len(exclude)} earlier filings excluded).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

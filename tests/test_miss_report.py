import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import miss_report  # noqa: E402


def make_record(ticker, form, date, key, text, size=None):
    return {
        "filing": {"ticker": ticker, "form": form, "filed_date": date, "items": [], "primary_url": "u"},
        "sections": [
            {"key": "1A", "title": "1A", "text": "x" * 9000, "source": "primary", "char_count": 9000},
            {"key": key, "title": key, "text": text, "source": "primary", "char_count": size or len(text)},
        ],
    }


PAGE = "Management's discussion and analysis appears on pages 46-160."
NAME = "The index to MD&A is presented in the Financial Table of Contents."


def test_pointer_section_is_non_standard_and_records_its_kind():
    result = miss_report.check_filing(make_record("JPM", "10-K", "2025-02-14", "7", PAGE))
    assert result["outcome"] == miss_report.NON_STANDARD
    assert result["pointers"]["7"][0] == "page"


def test_short_section_that_is_not_a_pointer_is_still_a_miss():
    result = miss_report.check_filing(make_record("ZZZ", "10-K", "2025-02-14", "7", "Short but not a pointer."))
    assert result["outcome"] == miss_report.MISS
    assert result["pointers"] == {}


def test_layout_groups_count_per_ticker_and_form_and_name_the_kinds():
    records = [
        make_record("CVX", "10-K", "2022-02-24", "7", PAGE),
        make_record("CVX", "10-K", "2023-02-23", "7", NAME),
        make_record("CVX", "10-K", "2026-02-24", "7", NAME),
        make_record("JPM", "10-K", "2025-02-14", "7", PAGE),
    ]
    items = [(r, miss_report.check_filing(r)) for r in records]
    groups = {(g["ticker"], g["form"]): g for g in miss_report.layout_groups(items)}

    cvx = groups["CVX", "10-K"]
    assert cvx["filings"] == 3
    assert cvx["keys"] == {"7"}
    assert dict(cvx["kinds"]) == {"name": 2, "page": 1}
    assert cvx["example_date"] == "2026-02-24"  # the latest filing is the example
    assert cvx["example_text"].startswith("The index to MD&A is presented in")
    assert groups["JPM", "10-K"]["filings"] == 1
    assert list(groups) == [("CVX", "10-K"), ("JPM", "10-K")]  # sorted by company


def test_pointer_kind_says_how_a_flagged_section_points_elsewhere():
    toc = "Introduction | 4\nExecutive Overview | 5\nConsolidated Results | 9"
    assert miss_report.pointer_kind(toc) == "toc"
    assert miss_report.pointer_kind(PAGE) == "page"
    assert miss_report.pointer_kind(NAME) == "name"

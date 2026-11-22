import classify


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
    result = classify.check_filing(make_record("JPM", "10-K", "2025-02-14", "7", PAGE))
    assert result["outcome"] == classify.NON_STANDARD
    assert result["pointers"]["7"][0] == "page"


def test_short_section_that_is_not_a_pointer_is_still_a_miss():
    result = classify.check_filing(make_record("ZZZ", "10-K", "2025-02-14", "7", "Short but not a pointer."))
    assert result["outcome"] == classify.MISS
    assert result["pointers"] == {}


def test_pointer_kind_says_how_a_flagged_section_points_elsewhere():
    toc = "Introduction | 4\nExecutive Overview | 5\nConsolidated Results | 9"
    assert classify.pointer_kind(toc) == "toc"
    assert classify.pointer_kind(PAGE) == "page"
    assert classify.pointer_kind(NAME) == "name"


def test_earnings_8k_without_a_2_02_heading_is_a_mismatch():
    record = {
        "filing": {"form": "8-K", "items": ["2.02", "9.01"]},
        "sections": [{"key": "EX-99.1", "title": "EX-99.1", "text": "x" * 2000, "source": "EX-99.1", "char_count": 2000}],
        "item_keys_found": ["2.01", "9.01"],
    }
    result = classify.check_filing(record)
    assert result["mismatch"] is True
    assert result["outcome"] == classify.OK


def test_8k_that_is_not_an_earnings_8k_is_not_checked():
    record = {"filing": {"form": "8-K", "items": ["5.02"]}, "sections": [], "item_keys_found": ["5.02"]}
    assert classify.check_filing(record) is None

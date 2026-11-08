import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import spot_check  # noqa: E402


def make_record(ticker, form, date, keys=None, size=6000):
    """A fake ingest record with every expected section present and long enough."""
    keys = keys or spot_check.EXPECTED[form]
    sections = [{"key": k, "title": k, "text": "x" * size, "source": "primary", "char_count": size} for k in keys]
    items = []
    if form == "8-K":
        items = ["2.02"]
        sections.append({"key": "EX-99.1", "title": "EX-99.1", "text": "y" * size, "source": "EX-99.1", "char_count": size})
    return {
        "filing": {"ticker": ticker, "form": form, "filed_date": date, "items": items, "primary_url": f"https://x/{ticker}"},
        "sections": sections,
    }


def make_records():
    tickers = [f"T{i:02d}" for i in range(25)]
    return [make_record(t, form, f"2024-0{m}-01") for t in tickers for form in ("10-K", "10-Q", "8-K") for m in (1, 2)]


# ---- Wilson interval -------------------------------------------------------


def test_wilson_all_correct_is_not_zero_width():
    low, high = spot_check.wilson_interval(20, 20)
    assert low == pytest.approx(0.8389, abs=0.001)
    assert high == pytest.approx(1.0)  # never above 100%


def test_wilson_half_correct_is_symmetric():
    low, high = spot_check.wilson_interval(10, 20)
    assert low == pytest.approx(0.2993, abs=0.001)
    assert high == pytest.approx(0.7007, abs=0.001)


# ---- picking the sample ----------------------------------------------------


def test_pick_is_the_same_every_time_with_the_same_seed():
    first = [r["filing"]["ticker"] + r["filing"]["form"] for r in spot_check.pick_filings(make_records())]
    second = [r["filing"]["ticker"] + r["filing"]["form"] for r in spot_check.pick_filings(make_records())]
    assert first == second


def test_pick_spreads_across_forms_and_companies():
    chosen = spot_check.pick_filings(make_records())
    assert len(chosen) == 20
    forms = [r["filing"]["form"] for r in chosen]
    assert (forms.count("10-K"), forms.count("10-Q"), forms.count("8-K")) == (7, 7, 6)
    assert len({r["filing"]["ticker"] for r in chosen}) == 20  # 20 different companies


def test_pick_skips_filings_missing_an_expected_section():
    records = [make_record("A", "10-K", "2024-01-01", keys=["1A", "7"])]  # no 7A
    assert spot_check.pick_filings(records) == []


# ---- the markdown file ------------------------------------------------------


def test_snippets_show_first_and_last_200_chars_or_the_whole_short_text():
    long_text = "a" * 200 + "MIDDLE" * 100 + "z" * 200
    first, _, last = spot_check.snippet_lines(long_text)
    assert "a" * 200 in first and "MIDDLE" not in first
    assert "z" * 200 in last and "MIDDLE" not in last
    assert spot_check.snippet_lines("short text") == ["> **full text:** short text"]


def test_rendered_file_has_a_checkbox_under_every_section_and_scores_back():
    chosen = spot_check.pick_filings(make_records())
    markdown = spot_check.render(chosen)
    reviews, errors = spot_check.read_reviews(markdown)
    assert errors == []
    assert len(reviews) == 7 * 3 + 7 * 2 + 6 * 2  # sections to check
    assert not any(r.ticked for r in reviews)

    ticked_one = markdown.replace("- [ ] correct", "- [x] correct", 1)
    assert sum(r.ticked for r in spot_check.read_reviews(ticked_one)[0]) == 1


# ---- reading your ticks back (--score) ----------------------------------------

# Regression: the checkbox lines below are the exact formats from a real review file.
# The old scorer needed "correct" to end the line, so every line with a note was skipped.
REVIEWED = """# Spot check

## 1. AAA 10-K 2025-01-01

https://example.com/aaa

### 1A: 65,809 chars

> **first 200:** text

- [x] correct

### 7: 45,534 chars

> **full text:** text

- [x] correct | 35 Table of Contents footer

### 7A: 2,963 chars

- [x] correct footer junk: "27 Table of Contents"

## 2. BBB 10-Q 2024-05-01

https://example.com/bbb

### I-2: 21,729 chars

- [x] correct 9 footer at end

### II-1A: 19,082 chars

- [ ] correct | WRONG END: runs into the next item for a few hundred characters because the filer puts a long unrelated discussion right after it

## 3. CCC 8-K 2023-01-24

https://example.com/ccc

### 2.02: 582 chars

- [ ] correct

### EX-99.1: 10,878 chars

- [x] correct
"""


def test_every_checkbox_format_is_counted_including_notes():
    reviews, errors = spot_check.read_reviews(REVIEWED)
    assert errors == []
    assert [(r.ticker, r.form, r.key, r.ticked) for r in reviews] == [
        ("AAA", "10-K", "1A", True),
        ("AAA", "10-K", "7", True),  # note after "correct"
        ("AAA", "10-K", "7A", True),  # junk with quotes after "correct"
        ("BBB", "10-Q", "I-2", True),  # number after "correct"
        ("BBB", "10-Q", "II-1A", False),  # long note, unticked: used to vanish
        ("CCC", "8-K", "2.02", False),  # plain unticked box
        ("CCC", "8-K", "EX-99.1", True),
    ]


def test_score_counts_all_sections_and_unticked_ones(tmp_path, capsys):
    path = tmp_path / "spot_check.md"
    path.write_text(REVIEWED, encoding="utf-8")
    assert spot_check.score(path) == 0
    out = capsys.readouterr().out
    assert "Sections reviewed: 7   correct (ticked): 5   wrong (unticked): 2" in out
    assert "Accuracy: 71.4% (5/7)" in out
    assert "By form: 10-K 3/3, 10-Q 1/2, 8-K 1/2" in out


def test_verbose_lists_every_section(tmp_path, capsys):
    path = tmp_path / "spot_check.md"
    path.write_text(REVIEWED, encoding="utf-8")
    spot_check.score(path, verbose=True)
    lines = capsys.readouterr().out.splitlines()
    assert "AAA | 10-K | 7 | correct" in lines
    assert "BBB | 10-Q | II-1A | wrong" in lines
    assert "CCC | 8-K | 2.02 | wrong" in lines
    assert sum(1 for line in lines if line.count(" | ") == 3) == 7


def test_section_without_a_checkbox_is_an_error_and_no_score(tmp_path, capsys):
    broken = REVIEWED.replace('- [x] correct footer junk: "27 Table of Contents"\n', "")
    path = tmp_path / "spot_check.md"
    path.write_text(broken, encoding="utf-8")
    assert spot_check.score(path) == 1
    out = capsys.readouterr().out
    assert "ERROR" in out and "AAA 10-K 7A has no '- [ ] correct' line" in out
    assert "Accuracy" not in out  # no score is printed


def test_two_checkboxes_under_one_section_is_an_error():
    doubled = REVIEWED.replace("- [x] correct | 35 Table of Contents footer", "- [x] correct\n\n- [ ] correct")
    reviews, errors = spot_check.read_reviews(doubled)
    assert any("no section header above it" in e for e in errors)
    assert any("counts don't add up" in e for e in errors)


def test_an_unrecognised_checkbox_is_an_error_not_silently_skipped():
    odd = REVIEWED.replace("- [ ] correct\n\n### EX-99.1", "- [~] correct\n\n### EX-99.1")
    _, errors = spot_check.read_reviews(odd)
    assert any("checkbox not recognised" in e for e in errors)
    assert any("CCC 8-K 2.02 has no" in e for e in errors)


def test_a_new_sample_never_repeats_filings_already_reviewed():
    records = make_records()
    first = spot_check.pick_filings(records)
    reviewed = spot_check.reviewed_filings(spot_check.render(first))
    assert len(reviewed) == 20
    second = spot_check.pick_filings(records, seed=7, exclude=reviewed)
    picked = {(r["filing"]["ticker"], r["filing"]["form"], r["filing"]["filed_date"]) for r in second}
    assert len(second) == 20
    assert not picked & reviewed

import pytest

from parse import find_cross_references, html_to_text, split_sections


def non_empty_lines(text: str) -> list[str]:
    return [line for line in text.split("\n") if line]


def test_script_and_style_removed():
    html = "<html><head><style>p {color: red}</style></head><body><script>alert(1)</script><p>Hello</p></body></html>"
    assert html_to_text(html) == "Hello"


def test_head_and_title_removed():
    html = (
        "<html><head><title>aapl-20250927</title><meta charset='utf-8'></head>"
        "<body><p>Cover page</p></body></html>"
    )
    assert html_to_text(html) == "Cover page"


def test_title_removed_without_head():
    assert html_to_text("<title>aapl-20250927</title><p>Cover page</p>") == "Cover page"


def test_display_none_removed():
    html = (
        '<div style="display:none">secret</div>'
        '<div style="color: red; DISPLAY : NONE;">also secret</div>'
        "<p>visible</p>"
    )
    assert html_to_text(html) == "visible"


def test_ix_header_removed():
    html = (
        "<body><ix:header><ix:hidden>dei:EntityCentralIndexKey 0000320193</ix:hidden></ix:header>"
        "<p>Annual report</p></body>"
    )
    assert html_to_text(html) == "Annual report"


def test_hidden_div_containing_ix_header_removed():
    html = '<div style="display:none"><ix:header>meta</ix:header></div><p>body</p>'
    assert html_to_text(html) == "body"


def test_block_elements_on_own_lines():
    html = (
        "<div><p>one</p><p>two</p>three<br>four</div>"
        "<ul><li>a</li><li>b</li></ul><h2>Head</h2>"
    )
    assert non_empty_lines(html_to_text(html)) == ["one", "two", "three", "four", "a", "b", "Head"]


def test_inline_tags_stay_on_one_line():
    assert html_to_text("<p>Net <b>sales</b> grew <span>5</span> percent</p>") == "Net sales grew 5 percent"


def test_source_newlines_inside_paragraph_become_spaces():
    assert html_to_text("<p>first\n   second</p>") == "first second"


def test_table_rows_become_lines():
    html = (
        "<table>"
        "<tr><th>Item</th><th>2022</th></tr>"
        "<tr><td>Revenue</td><td>100</td></tr>"
        "</table>"
    )
    assert html_to_text(html) == "Item | 2022\nRevenue | 100"


def test_table_fragments_merged():
    html = (
        "<table><tr>"
        "<td>Net income</td><td>$</td><td>1,200</td>"
        "<td></td><td>$</td><td>(</td><td>56</td><td>)</td>"
        "<td>12</td><td>%</td>"
        "</tr></table>"
    )
    assert html_to_text(html) == "Net income | 1,200 | (56) | 12%"


def test_row_with_only_empty_cells_is_skipped():
    html = "<table><tr><td></td><td>$</td></tr><tr><td>a</td><td>b</td></tr></table>"
    assert html_to_text(html) == "a | b"


def test_characters_normalized():
    html = "<p>Apple\xa0Inc.​’s “best” year ‘ever’</p>"
    assert html_to_text(html) == "Apple Inc.'s \"best\" year 'ever'"


def test_runs_of_spaces_collapsed():
    assert html_to_text("<p>a      b \t  c</p>") == "a b c"


def test_three_or_more_newlines_become_two():
    html = "<p>top</p><br><br><br><br><p>bottom</p>"
    assert html_to_text(html) == "top\n\nbottom"


def test_accepts_bytes():
    assert html_to_text("<p>café</p>".encode("utf-8")) == "café"


# ---- split_sections -------------------------------------------------------

PROSE_ITEM_8 = (
    "Item 8 of this report contains the consolidated financial statements, the notes to them, "
    "and the report of the independent registered public accounting firm, all of which should be "
    "read together with this discussion."
)
assert len(PROSE_ITEM_8) > 150  # the test only means something if this is prose-length

TEN_K = f"""Apple Inc.
FORM 10-K
TABLE OF CONTENTS
Item 1. | Business | 1
Item 1A. | Risk Factors | 5
Item 7. | Management's Discussion and Analysis | 21
Item 7A. | Quantitative and Qualitative Disclosures About Market Risk | 27
Item 8. | Financial Statements and Supplementary Data | 28

PART I
Item 1. Business
The Company designs, manufactures and markets smartphones.

Item 1A. Risk Factors
The Company's business can be affected by many risks.

PART II
Item 7. Management's Discussion and Analysis of Financial Condition and Results of Operations
Net sales increased during the year.
{PROSE_ITEM_8}

Item 7A. Quantitative and Qualitative Disclosures About Market Risk
The Company is exposed to interest rate risk.

Item 8. Financial Statements and Supplementary Data
Consolidated Statements of Operations follow.
"""


def sections_by_key(text):
    return {s.key: s for s in split_sections(text, "10-K")}


def test_body_copy_beats_table_of_contents():
    sections = split_sections(TEN_K, "10-K")
    assert [s.key for s in sections] == ["1", "1A", "7", "7A", "8"]
    risk = sections_by_key(TEN_K)["1A"]
    assert "many risks" in risk.text
    assert "| 5" not in risk.text


def test_7_and_7a_do_not_merge():
    sections = sections_by_key(TEN_K)
    assert "Net sales increased" in sections["7"].text
    assert "interest rate risk" not in sections["7"].text
    assert sections["7A"].text == "The Company is exposed to interest rate risk."


def test_prose_mentioning_item_8_is_not_a_heading():
    sections = sections_by_key(TEN_K)
    assert PROSE_ITEM_8 in sections["7"].text
    assert sections["8"].text == "Consolidated Statements of Operations follow."


def test_titles_come_from_lookup_table():
    sections = sections_by_key(TEN_K)
    assert sections["7"].title == (
        "Management's Discussion and Analysis of Financial Condition and Results of Operations"
    )
    assert sections["1A"].title == "Risk Factors"
    assert sections["1A"].source == "primary"


def test_char_count():
    section = sections_by_key(TEN_K)["7A"]
    assert section.char_count == len(section.text)


def test_space_before_period_and_uppercase_headings():
    text = "ITEM 1A RISK FACTORS\nRisks here.\nItem 12 . Security Ownership\nOwners here."
    sections = sections_by_key(text)
    assert sections["1A"].text == "Risks here."
    assert sections["12"].text == "Owners here."


def test_repeated_page_headers_do_not_split_a_section():
    # Regression (MSFT 10-K): every page repeats "PART II / Item 7" as a running header.
    # Each repeat used to end the section, so only the longest single page was kept.
    text = """Item 7. | Management's Discussion and Analysis | 34
Item 7A. | Quantitative and Qualitative Disclosures About Market Risk | 49

PART II
Item 7
ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS
Page one of MD&A.
34
PART II
Item 7
Page two of MD&A, which is a bit longer than page one.
35
PART II
Item 7A
ITEM 7A. QUANTITATIVE AND QUALITATIVE DISCLOSURES ABOUT MARKET RISK
Market risk text.
"""
    sections = sections_by_key(text)
    assert "Page one of MD&A." in sections["7"].text
    assert "Page two of MD&A" in sections["7"].text
    assert "Market risk text." not in sections["7"].text
    assert sections["7A"].text.endswith("Market risk text.")


def test_reserved_item_with_no_body_is_not_returned():
    # Regression (JPM, KO): "Item 6. Reserved" has nothing under it, and its longer TOC
    # line used to win, so an empty section "6" came back first.
    text = """Item 6. | Reserved | 33
Item 7. | Management's Discussion and Analysis | 33

Item 6. Reserved

Item 7. Management's Discussion and Analysis
Net revenue grew.
"""
    sections = split_sections(text, "10-K")
    assert [s.key for s in sections] == ["7"]
    assert all(s.text for s in sections)


def test_short_body_beats_longer_toc_line():
    text = """Item 4. | Mine Safety Disclosures (a very long table of contents line) | 31
Item 5. | Market | 32

Item 4. Mine Safety
N/A
Item 5. Market
Our stock trades on Nasdaq.
"""
    assert sections_by_key(text)["4"].text == "N/A"


def test_other_forms_not_supported_yet():
    with pytest.raises(NotImplementedError):
        split_sections(TEN_K, "S-1")


# ---- 10-Q parts -----------------------------------------------------------

TEN_Q = """Item 1. This line comes before any PART and must be ignored.
TABLE OF CONTENTS
PART I — FINANCIAL INFORMATION
Item 1. | Financial Statements | 1
Item 2. | Management's Discussion and Analysis | 15
PART II — OTHER INFORMATION
Item 1. | Legal Proceedings | 30
Item 1A. | Risk Factors | 30

PART I — FINANCIAL INFORMATION
Item 1. Financial Statements
Condensed consolidated statements of operations.
Item 2. Management's Discussion and Analysis of Financial Condition and Results of Operations
Revenue grew 8% in the quarter.

PART II — OTHER INFORMATION
Item 1. Legal Proceedings
The Company is party to various legal proceedings.
Item 1A. Risk Factors
There have been no material changes to the risk factors in our 10-K.
"""


def test_10q_keys_include_the_part():
    sections = split_sections(TEN_Q, "10-Q")
    assert [s.key for s in sections] == ["I-1", "I-2", "II-1", "II-1A"]


def test_10q_part_i_and_part_ii_item_1_stay_separate():
    sections = {s.key: s for s in split_sections(TEN_Q, "10-Q")}
    assert sections["I-1"].text == "Condensed consolidated statements of operations."
    assert sections["II-1"].text == "The Company is party to various legal proceedings."
    # Known limit: the "PART II" banner stays at the end of the previous section.
    # PART lines don't end sections, because some filers repeat "PART I" as a page header.
    assert sections["I-2"].text == "Revenue grew 8% in the quarter.\n\nPART II — OTHER INFORMATION"
    assert sections["II-1A"].title == "Risk Factors"


def test_10q_part_iii_is_not_part_ii():
    text = "PART I\nItem 2. MD&A\nMD&A text.\nPART III\nItem 1. Legal\nShould stay in Part I."
    sections = {s.key: s for s in split_sections(text, "10-Q")}
    # "PART III" isn't a 10-Q part, so we are still in Part I, and "I-1" is a new item there.
    assert "II-1" not in sections
    assert sections["I-1"].text == "Should stay in Part I."


# ---- 8-K items ------------------------------------------------------------

EIGHT_K = """UNITED STATES SECURITIES AND EXCHANGE COMMISSION
FORM 8-K
Item 2.02 Results of Operations and Financial Condition.
On October 30, 2025, Apple Inc. issued a press release regarding its financial results.
Item 7.01 Regulation FD Disclosure.
The information in this Item is furnished, not filed.
Item 9.01 Financial Statements and Exhibits.
(d) Exhibits. | 99.1 | Press release issued by Apple Inc. on October 30, 2025.
"""


def test_8k_items():
    sections = split_sections(EIGHT_K, "8-K")
    assert [s.key for s in sections] == ["2.02", "7.01", "9.01"]
    assert sections[0].title == "Results of Operations and Financial Condition"
    assert sections[0].text.startswith("On October 30, 2025")


def test_8k_item_number_is_not_cut_at_the_dot():
    # The 10-K regex would read "Item 2.02" as item "2"; the 8-K regex must not.
    keys = [s.key for s in split_sections(EIGHT_K, "8-K")]
    assert "2" not in keys


# ---- find_cross_references ------------------------------------------------

# Trimmed from JPM's 2025 10-K: the items point into the annual report printed later.
JPM_STYLE = """Item 1A. Risk Factors.
{risks}

Item 7. Management's Discussion and Analysis of Financial Condition and Results of Operations.
Management's discussion and analysis of financial condition and results of operations, entitled "Management's discussion and analysis," appears on pages 46–160.

Item 7A. Quantitative and Qualitative Disclosures About Market Risk.
Refer to the Market Risk Management section of Management's discussion and analysis on pages 133-142.

Item 10. Directors, Executive Officers and Corporate Governance.
Incorporated by reference to the Proxy Statement, see pages 4-20.
""".format(risks="The Firm faces many risks. " * 100)


def test_jpm_style_cross_references_detected():
    sections = split_sections(JPM_STYLE, "10-K")
    # 1A has real text; Item 10's proxy pointer is normal and ignored.
    assert find_cross_references(sections) == ["7", "7A"]


def test_standard_filing_has_no_cross_references():
    assert find_cross_references(split_sections(TEN_K, "10-K")) == []


def test_short_section_without_page_reference_is_not_a_cross_reference():
    text = "Item 7A. Quantitative and Qualitative Disclosures About Market Risk\nNot applicable."
    assert find_cross_references(split_sections(text, "10-K")) == []

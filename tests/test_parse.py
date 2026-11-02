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


# ---- stacked 8-K headings and table-of-contents-only sections ----------------

STACKED_8K = """Item 2.02 | Results of Operations and Financial Condition
Item 7.01 | Regulation FD Disclosure
The following information is furnished pursuant to both Item 2.02 and Item 7.01.
Item 9.01 | Financial Statements and Exhibits
(d) Exhibits.
"""


def test_8k_stacked_headings_share_the_next_body():
    # Regression (XOM 8-K): both headings sit on one shared body; 2.02 used to come back empty.
    sections = sections_by_key_for(STACKED_8K, "8-K")
    assert sections["2.02"].text == sections["7.01"].text
    assert sections["2.02"].text.startswith("The following information is furnished")
    assert sections["9.01"].text == "(d) Exhibits."


def test_8k_last_heading_with_no_text_is_still_dropped():
    assert [s.key for s in split_sections("Item 2.02 Results\nText.\nItem 9.01 Exhibits\n", "8-K")] == ["2.02"]


def test_10k_empty_item_does_not_borrow_the_next_body():
    text = "Item 6. Reserved\nItem 7. Management's Discussion and Analysis\nSales grew.\n"
    assert [s.key for s in split_sections(text, "10-K")] == ["7"]


def sections_by_key_for(text, form):
    return {s.key: s for s in split_sections(text, form)}


# JPM's 10-Q: the body has no "Item 2" heading, so the only copy of Item 2 is the TOC entry.
JPM_STYLE_10Q = """Part I
Item 1. | Financial Statements | 3
Item 2. | Management's Discussion and Analysis of Financial Condition and Results of Operations
Introduction | 4
Executive Overview | 5
Consolidated Results of Operations | 9
Item 3. | Quantitative and Qualitative Disclosures About Market Risk | 201

Part I
Item 3. Quantitative and Qualitative Disclosures About Market Risk.
Refer to the Market Risk Management section of the MD&A.
"""


def test_toc_only_section_is_a_cross_reference():
    sections = split_sections(JPM_STYLE_10Q, "10-Q")
    assert find_cross_references(sections) == ["I-2"]


def test_prose_section_with_a_few_numbers_is_not_a_toc():
    text = "Part I\nItem 2. MD&A\nRevenue grew in the quarter.\nSegments | 4\nCosts rose as well.\n"
    assert find_cross_references(split_sections(text, "10-Q")) == []


# ---- long headings and a missing PART I line ---------------------------------

def test_long_heading_with_standard_title_is_accepted():
    # Regression (COST 10-K): "Item 7—... (amounts in millions, ...)" is over 150 characters.
    heading = (
        "Item 7—Management's Discussion and Analysis of Financial Condition and Results of Operations "
        "(amounts in millions, except per share, share, percentage and warehouse data)"
    )
    assert len(heading) > 150
    text = f"Item 6—Reserved\n{heading}\nOverview of the year.\nItem 7A—Quantitative and Qualitative Disclosures About Market Risk\nRates.\n"
    assert sections_by_key(text)["7"].text == "Overview of the year."


def test_missing_part_i_line_in_the_body_of_a_10q():
    # Regression (NFLX 10-Q): the table of contents ends in Part II and the body has no
    # "PART I" line, so the body's Items 2-4 used to be keyed II-2..II-4.
    text = """Part I. Financial Information
Item 1. | Consolidated Financial Statements
Item 2. | Management's Discussion and Analysis | 26
Item 3. | Quantitative and Qualitative Disclosures About Market Risk | 38
Part II. Other Information
Item 1. | Legal Proceedings | 39
Item 2. | Unregistered Sales of Equity Securities and Use of Proceeds | 40
Item 6. | Exhibits | 40

Consolidated financial statements go here.
Item 2.Management's Discussion and Analysis of Financial Condition and Results of Operations
Streaming revenue grew.
Item 3.Quantitative and Qualitative Disclosures About Market Risk
Rates.
PART II. OTHER INFORMATION
Item 1.Legal Proceedings
None.
Item 2.Unregistered Sales of Equity Securities and Use of Proceeds
None.
"""
    sections = sections_by_key_for(text, "10-Q")
    assert sections["I-2"].text == "Streaming revenue grew."
    assert sections["I-3"].text.startswith("Rates.")  # the PART II banner stays attached (known limit)
    assert sections["II-1"].text == "None."


# ---- annual-report-style 10-Ks: no "Item N" headings, sections headed by their titles ----

def make_annual_report_style():
    # Like MCD and INTC: the front contents lists titles with page numbers, the body is
    # headed by the standard titles, and "Item N" only appears in an index at the very end.
    # The MD&A title is repeated as a page header, which must not cut the section.
    return f"""FORM 10-K
Contents
Management's Discussion and Analysis | 3
Risk Factors | 9

MANAGEMENT'S DISCUSSION AND ANALYSIS
{"Sales grew in every region. " * 60}
Management's Discussion and Analysis
{"Operating margin improved. " * 60}
RISK FACTORS
{"Many risks could hurt results. " * 60}
Properties
Our headquarters is in Oak Brook.

FORM 10-K CROSS-REFERENCE INDEX
Item 1A | Risk Factors | Page 9
Item 7 | Management's Discussion and Analysis of Financial Condition and Results of Operations | Page 3
"""


def test_annual_report_style_10k_is_split_by_title():
    # Regression (MCD, INTC): 12 of the 22 misses in the 25-company run. The Item headings
    # are only in the index, so no section was found (or only a tiny index copy).
    sections = sections_by_key(make_annual_report_style())
    assert {"7", "1A"} <= set(sections)
    assert "Sales grew in every region." in sections["7"].text
    assert "Operating margin improved." in sections["7"].text  # the repeated page header didn't cut it
    assert "Many risks" not in sections["7"].text
    assert "Many risks could hurt results." in sections["1A"].text
    assert "Oak Brook" not in sections["1A"].text
    assert "| Page 9" not in sections["1A"].text


def test_title_fallback_is_not_used_when_item_headings_work():
    # A normal 10-K: the Item headings give three real sections, so a standalone
    # "Properties" line inside Item 7 must stay inside Item 7.
    body = "Words that make the section long enough to count. " * 30
    text = f"Item 1. Business\n{body}\nItem 1A. Risk Factors\n{body}\nItem 7. MD&A\n{body}\nProperties\nmore\n"
    sections = sections_by_key(text)
    assert sorted(sections) == ["1", "1A", "7"]
    assert sections["7"].text.endswith("Properties\nmore")


def test_form_10k_cover_line_is_not_a_form_10k_summary_heading():
    text = make_annual_report_style()
    assert "16" not in sections_by_key(text)


# ---- title variants in long headings, and page footers ------------------------

def test_long_heading_with_a_variant_title_is_accepted():
    # Regression (COST 10-K 2021-2024): the heading says "Financial Conditions" (plural),
    # the standard title says "Financial Condition". Only the first words are compared.
    heading = (
        "Item 7—Management's Discussion and Analysis of Financial Conditions and Results of Operations "
        "(amounts in millions, except per share, share, membership fee, and warehouse count data)"
    )
    assert len(heading) > 150
    text = f"Item 6—Reserved\n{heading}\nOverview of the year.\nItem 7A—Quantitative and Qualitative Disclosures About Market Risk\nRates.\n"
    assert sections_by_key(text)["7"].text == "Overview of the year."


def test_long_prose_starting_with_item_and_a_number_is_still_not_a_heading():
    prose = "Item 7 of this report contains management's analysis, which should be read together with the financial statements and notes."
    text = f"Item 7. Management's Discussion and Analysis\nFirst.\n{prose}\nSecond.\n"
    assert sections_by_key(text)["7"].text == f"First.\n{prose}\nSecond."


def make_pages(footer_for_page, n_pages=6):
    """A body of n_pages pages, each followed by its own footer line (None = no footer)."""
    pages = []
    for page in range(1, n_pages + 1):
        pages.append(f"Text of page {page} goes here.")
        footer = footer_for_page(page)
        if footer:
            pages.append(footer)
    return "Item 7. Management's Discussion and Analysis\n\n" + "\n\n".join(pages) + "\n"


def test_page_footers_are_removed_from_sections():
    # Regression (MCD, INTC): both footer styles leaked into the section text.
    mcd = make_pages(lambda p: f"McDonald's Corporation 2025 Annual Report {20 + p}")
    intc = make_pages(lambda p: f"MD&A | {30 + p}")
    for text in (mcd, intc):
        body = sections_by_key(text)["7"].text
        assert "Annual Report" not in body and "MD&A |" not in body
        assert "Text of page 1 goes here." in body and "Text of page 6 goes here." in body


def test_a_label_and_number_that_does_not_repeat_is_kept():
    text = make_pages(lambda p: f"Source: company filings {p}" if p == 3 else None)  # only once
    assert "Source: company filings 3" in sections_by_key(text)["7"].text


def test_repeated_label_with_a_constant_number_is_kept():
    # Same line 6 times but the number never rises: not pagination (e.g. "Note 5").
    text = make_pages(lambda p: "See Note 5")
    assert text.count("See Note 5") == 6
    assert sections_by_key(text)["7"].text.count("See Note 5") == 6


def test_table_rows_are_not_mistaken_for_footers():
    # Consecutive two-cell rows whose numbers rise: not alone on their own line, so kept.
    rows = "\n".join(f"Revenue year | {n}" for n in range(1, 8))
    text = f"Item 7. Management's Discussion and Analysis\n\n{rows}\n"
    assert sections_by_key(text)["7"].text == rows

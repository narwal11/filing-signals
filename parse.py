import re
import warnings
from dataclasses import dataclass

from bs4 import BeautifulSoup, NavigableString, XMLParsedAsHTMLWarning

# SEC inline XBRL filings are XHTML, so BeautifulSoup warns that XML is being parsed
# as HTML. The lxml HTML parser handles them fine, so silence only this warning.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Tags that should each start and end on their own line. <br> is handled separately.
BLOCK_TAGS = ["p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6"]

# Inline XBRL filings hide their metadata in elements styled "display:none".
HIDDEN_STYLE = re.compile(r"display\s*:\s*none", re.IGNORECASE)

# One-to-one character fixes. Mapping a character to None deletes it.
CHAR_FIXES = str.maketrans(
    {
        "\xa0": " ",  # non-breaking space
        "​": None,  # zero-width space
        "‌": None,  # zero-width non-joiner
        "‍": None,  # zero-width joiner
        "⁠": None,  # word joiner
        "﻿": None,  # byte order mark / zero-width no-break space
        "‘": "'",  # curly single quotes
        "’": "'",
        "“": '"',  # curly double quotes
        "”": '"',
    }
)


def _remove(tags):
    for tag in tags:
        # A tag nested inside an already-removed tag is gone; skip it.
        if not tag.decomposed:
            tag.decompose()


def _merge_cells(cells: list[str]) -> list[str]:
    """Clean up the fragments that financial tables split into separate cells.

    Typical row: ["Net income", "$", "(", "56", ")", "12", "%"]
    Result:      ["Net income", "(56)", "12%"]
    """
    merged = []
    prefix = ""  # holds an opening "(" until the number after it shows up
    for cell in cells:
        if cell in ("", "$"):
            continue  # spacer cells and lone dollar signs carry no information
        if cell == "(":
            prefix = "("
        elif cell in (")", "%", ")%") and merged:
            merged[-1] += cell  # glue the closing piece onto the number before it
        else:
            merged.append(prefix + cell)
            prefix = ""
    return merged


def _table_to_text(table) -> str:
    """One line per row, cells joined by ' | '."""
    lines = []
    for row in table.find_all("tr"):
        cells = [" ".join(c.get_text(" ").split()) for c in row.find_all(["td", "th"])]
        cells = _merge_cells(cells)
        if cells:
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def html_to_text(html: bytes | str) -> str:
    """Convert a filing's HTML into plain text, one block per line."""
    soup = BeautifulSoup(html, "lxml")

    # 1. Remove things that are not visible text.
    #    <head> holds the <title> (e.g. "aapl-20250927") and other non-body content.
    _remove(soup.find_all(["head", "title", "script", "style", "ix:header"]))
    _remove(soup.find_all(style=HIDDEN_STYLE))

    # 2. Fix characters and turn source-code whitespace into single spaces.
    #    In HTML, a newline inside a paragraph is just a space, so it must not
    #    become a line break. (Skip comments and the like: only real text nodes.)
    for node in soup.find_all(string=True):
        if type(node) is NavigableString:
            cleaned = node.translate(CHAR_FIXES)
            node.replace_with(re.sub(r"\s+", " ", cleaned))

    # 3. Replace each table with its text. Reversed, so inner tables go first.
    for table in reversed(soup.find_all("table")):
        table.replace_with(NavigableString("\n" + _table_to_text(table) + "\n"))

    # 4. Put block elements on their own lines.
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for tag in soup.find_all(BLOCK_TAGS):
        tag.insert_before("\n")
        tag.insert_after("\n")

    # 5. Tidy: collapse runs of spaces, then runs of blank lines.
    lines = [" ".join(line.split()) for line in soup.get_text().split("\n")]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---- splitting text into item sections ----------------------------------


@dataclass(frozen=True)
class Section:
    key: str  # item number, e.g. "1A"
    title: str  # standard item name, e.g. "Risk Factors"
    text: str  # the section body, without its heading line
    source: str = "primary"  # which document it came from (8-Ks will also use exhibits)

    @property
    def char_count(self) -> int:
        return len(self.text)


# Standard 10-K item names. Keys not in this table are ignored when splitting.
TEN_K_ITEMS = {
    "1": "Business",
    "1A": "Risk Factors",
    "1B": "Unresolved Staff Comments",
    "1C": "Cybersecurity",
    "2": "Properties",
    "3": "Legal Proceedings",
    "4": "Mine Safety Disclosures",
    "5": "Market for Registrant's Common Equity, Related Stockholder Matters "
    "and Issuer Purchases of Equity Securities",
    "6": "[Reserved]",
    "7": "Management's Discussion and Analysis of Financial Condition and Results of Operations",
    "7A": "Quantitative and Qualitative Disclosures About Market Risk",
    "8": "Financial Statements and Supplementary Data",
    "9": "Changes in and Disagreements with Accountants on Accounting and Financial Disclosure",
    "9A": "Controls and Procedures",
    "9B": "Other Information",
    "9C": "Disclosure Regarding Foreign Jurisdictions that Prevent Inspections",
    "10": "Directors, Executive Officers and Corporate Governance",
    "11": "Executive Compensation",
    "12": "Security Ownership of Certain Beneficial Owners and Management "
    "and Related Stockholder Matters",
    "13": "Certain Relationships and Related Transactions, and Director Independence",
    "14": "Principal Accountant Fees and Services",
    "15": "Exhibit and Financial Statement Schedules",
    "16": "Form 10-K Summary",
}

# Standard 10-Q item names. Item numbers repeat across parts, so the key includes the part.
TEN_Q_ITEMS = {
    "I-1": "Financial Statements",
    "I-2": "Management's Discussion and Analysis of Financial Condition and Results of Operations",
    "I-3": "Quantitative and Qualitative Disclosures About Market Risk",
    "I-4": "Controls and Procedures",
    "II-1": "Legal Proceedings",
    "II-1A": "Risk Factors",
    "II-2": "Unregistered Sales of Equity Securities and Use of Proceeds",
    "II-3": "Defaults Upon Senior Securities",
    "II-4": "Mine Safety Disclosures",
    "II-5": "Other Information",
    "II-6": "Exhibits",
}

# Standard 8-K item names.
EIGHT_K_ITEMS = {
    "1.01": "Entry into a Material Definitive Agreement",
    "1.02": "Termination of a Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership",
    "1.04": "Mine Safety - Reporting of Shutdowns and Patterns of Violations",
    "1.05": "Material Cybersecurity Incidents",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition",
    "2.03": "Creation of a Direct Financial Obligation",
    "2.04": "Triggering Events That Accelerate or Increase a Direct Financial Obligation",
    "2.05": "Costs Associated with Exit or Disposal Activities",
    "2.06": "Material Impairments",
    "3.01": "Notice of Delisting or Failure to Satisfy a Continued Listing Rule",
    "3.02": "Unregistered Sales of Equity Securities",
    "3.03": "Material Modification to Rights of Security Holders",
    "4.01": "Changes in Registrant's Certifying Accountant",
    "4.02": "Non-Reliance on Previously Issued Financial Statements",
    "5.01": "Changes in Control of Registrant",
    "5.02": "Departure of Directors or Certain Officers; Election of Directors; "
    "Appointment of Certain Officers; Compensatory Arrangements of Certain Officers",
    "5.03": "Amendments to Articles of Incorporation or Bylaws; Change in Fiscal Year",
    "5.04": "Temporary Suspension of Trading Under Registrant's Employee Benefit Plans",
    "5.05": "Amendment to Registrant's Code of Ethics, or Waiver of a Provision of the Code of Ethics",
    "5.06": "Change in Shell Company Status",
    "5.07": "Submission of Matters to a Vote of Security Holders",
    "5.08": "Shareholder Director Nominations",
    "6.01": "ABS Informational and Computational Material",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}

# Finds item heading lines such as "Item 1A. Risk Factors" or "Item 7A. | Quantitative... | 27".
# It is matched against ONE line at a time (fullmatch), so it must cover the whole line.
#
#   [ \t]*                optional indentation
#   (                     group 1 = the heading line without indentation (used for the length check)
#     item[ \t]+          the word "Item" and at least one space
#     (\d{1,2}[A-Z]?)     group 2 = the item number: 1-2 digits plus an optional letter (1, 1A, 7A, 15)
#     (?:[ \t.:|\-–—].*)? optionally: a separator (space . : | - en/em dash) then the rest of the line
#   )                     (if the optional part is absent, the line ends right after the number: "Item 7")
#
# Because a separator (or end of line) must follow the number, "Item 7A." gives "7A",
# never "7". And "Item 601(b)" doesn't match at all: 3 digits, then "(".
# IGNORECASE makes "ITEM 1A" match.
HEADING_RE = re.compile(r"[ \t]*(item[ \t]+(\d{1,2}[A-Z]?)(?:[ \t.:|\-–—].*)?)", re.IGNORECASE)

# 8-K items are numbered "2.02": same shape, but the number is (digits).(two digits).
# HEADING_RE can't be reused: it would read "Item 2.02" as item "2" followed by a "." separator.
EIGHT_K_ITEM_RE = re.compile(r"[ \t]*(item[ \t]+(\d{1,2}\.\d{2})(?:[ \t.:|\-–—].*)?)", re.IGNORECASE)

# Finds 10-Q part lines such as "PART I — FINANCIAL INFORMATION" or "Part II".
#   part[ \t]+   the word "Part" and at least one space
#   (I{1,2})     group 2 = the part: "I" or "II"
#   \b           the part must end there, so "PART III" doesn't count as "II"
PART_RE = re.compile(r"[ \t]*(part[ \t]+(I{1,2})\b.*)", re.IGNORECASE)

# A "heading" longer than this is really prose, e.g. "Item 8 of this report contains ...".
MAX_HEADING_LENGTH = 150

# form -> (item heading regex, title table, does the key include the PART?)
FORMS = {
    "10-K": (HEADING_RE, TEN_K_ITEMS, False),
    "10-Q": (HEADING_RE, TEN_Q_ITEMS, True),
    "8-K": (EIGHT_K_ITEM_RE, EIGHT_K_ITEMS, False),
}


# Forms where an item heading with no text of its own shares the next item's text.
SHARED_BODY_FORMS = {"8-K"}


def _words(text: str) -> str:
    """Lower-case and keep only letters and digits, one space apart: "Item 7—MD&A" -> "item 7 md a"."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _heading_match(regex: re.Pattern, line: str, titles: dict | None = None) -> re.Match | None:
    """Match a heading regex against a whole line, rejecting prose-length lines.

    A long line is still accepted if it starts "Item <number> <standard title>", e.g.
    "Item 7—Management's Discussion and Analysis ... (amounts in millions, except per share ...)".
    "Item 8 of this report contains ..." is prose and has no standard title right after the number.
    """
    match = regex.fullmatch(line)
    if not match:
        return None
    if len(match.group(1)) <= MAX_HEADING_LENGTH:
        return match
    if titles:
        line_words = _words(match.group(1))
        number_words = _words(match.group(2))
        # Only the first few words of the title are compared, so a variant ending still matches
        # (COST writes "Financial Conditions"; the standard title says "Financial Condition").
        if any(line_words.startswith(f"item {number_words} {_title_start(t)}") for t in titles.values()):
            return match
    return None


# How many words of a standard title a long heading line must start with.
TITLE_START_WORDS = 3


def _title_start(title: str) -> str:
    """The first words of a title, normalized: "Management's Discussion and Analysis of ..." -> "management s discussion"."""
    return " ".join(_words(title).split()[:TITLE_START_WORDS])


# ---- page footers -------------------------------------------------------

# A running page footer: some label, then the page number. Examples:
#   "McDonald's Corporation 2025 Annual Report 33"     -> label "McDonald's Corporation 2025 Annual Report", page 33
#   "MD&A | 36"                                         -> label "MD&A |", page 36
#   (.*[A-Za-z&].*?[^\d\s])   group 1 = the label: contains a letter and ends with a non-digit,
#                             so "Foo 2025" can't be read as label "Foo 2" and page 25
#   \s*(\d{1,3})             group 2 = the page number: 1-3 digits at the very end
FOOTER_RE = re.compile(r"(.*[A-Za-z&].*?[^\d\s])\s*(\d{1,3})")
MAX_FOOTER_LENGTH = 100
MIN_FOOTER_REPEATS = 5  # a footer shows up on page after page
MIN_RISING_PAGE_STEPS = 0.8  # share of consecutive occurrences whose page number goes up


def _find_page_footers(text: str) -> set[str]:
    """Return the exact lines that are running page footers.

    Deliberately conservative. A line only counts if ALL of these hold:
      - it has a label plus a trailing page number (FOOTER_RE),
      - it stands alone: a blank line before and after (table rows are consecutive lines),
      - the same label repeats at least 5 times, and the page number mostly goes up.
    Bare page numbers ("34") are NOT removed: they look too much like footnote markers.
    """
    lines = text.split("\n")
    by_label = {}  # label -> [(line, page number), ...] in document order
    for i, line in enumerate(lines):
        if not line or len(line) > MAX_FOOTER_LENGTH:
            continue
        alone = (i == 0 or not lines[i - 1]) and (i == len(lines) - 1 or not lines[i + 1])
        match = FOOTER_RE.fullmatch(line)
        if alone and match:
            by_label.setdefault(match.group(1), []).append((line, int(match.group(2))))

    footers = set()
    for entries in by_label.values():
        pages = [page for _, page in entries]
        rising = sum(1 for a, b in zip(pages, pages[1:]) if b > a)
        if len(entries) >= MIN_FOOTER_REPEATS and rising >= MIN_RISING_PAGE_STEPS * (len(pages) - 1):
            footers.update(line for line, _ in entries)
    return footers


def _strip_footers(body: str, footers: set[str]) -> str:
    if not footers:
        return body
    kept = "\n".join(line for line in body.split("\n") if line not in footers)
    return re.sub(r"\n{3,}", "\n\n", kept).strip()


def _build_sections(text: str, headings: list, titles: dict, form: str, footers: set[str]) -> list[Section]:
    """Turn heading positions (start of line, end of line, key) into sections."""
    # 2. Each heading appears twice: once in the table of contents, once in the body.
    #    A TOC entry is followed almost immediately by the next TOC entry, so its span
    #    is tiny. For each key, keep the occurrence with the longest span.
    #
    #    A span ends at the next heading with a DIFFERENT key. Some filers (e.g. MSFT)
    #    repeat a bare "Item 7" as a page header on every page; those repeats must not
    #    cut the section into one-page pieces.
    #
    #    The span is measured from the END of the heading line, so a long TOC line
    #    like "Item 6. | Reserved | 33" can't beat a short body copy on heading length.
    best = {}  # key -> (span length, start, heading end, section end)
    for i, (start, heading_end, key) in enumerate(headings):
        end = len(text)
        for next_start, _, next_key in headings[i + 1 :]:
            if next_key != key:
                end = next_start
                break
        if key not in best or end - heading_end > best[key][0]:
            best[key] = (end - heading_end, start, heading_end, end)

    # 3. Build the sections in the order they appear in the document.
    ordered = sorted(best.items(), key=lambda kv: kv[1][1])  # (key, (_, start, heading_end, end))
    bodies = [_strip_footers(text[heading_end:end].strip(), footers) for _, (_, _, heading_end, end) in ordered]

    # 8-Ks often stack headings over one shared body:
    #     Item 2.02 Results of Operations and Financial Condition
    #     Item 7.01 Regulation FD Disclosure
    #     The following information is furnished pursuant to both Item 2.02 and Item 7.01. ...
    # so an empty item takes the body of the next one (working backwards handles longer
    # stacks). Not done for 10-Ks: there an empty "Item 6. Reserved" is really empty.
    if form in SHARED_BODY_FORMS:
        for i in range(len(bodies) - 2, -1, -1):
            if not bodies[i]:
                bodies[i] = bodies[i + 1]

    # Skip items with nothing under the heading, e.g. "Item 6. [Reserved]".
    return [Section(key, titles[key], body) for (key, _), body in zip(ordered, bodies) if body]


# A real section is at least this long; index lines and pointers are not.
MIN_REAL_SECTION_CHARS = 1_000
# A normal 10-K has a dozen or more real sections. Fewer than this means the Item headings failed.
MIN_REAL_SECTIONS = 3
# A line that only STARTS a long standard title still counts ("Management's Discussion and
# Analysis"), but only for titles this long: "FORM 10-K" must not match "Form 10-K Summary".
MIN_WORDS_FOR_TITLE_PREFIX = 6


def _real_section_count(sections: list[Section]) -> int:
    return sum(1 for s in sections if s.char_count >= MIN_REAL_SECTION_CHARS)


def _find_title_headings(text: str, titles: dict) -> list:
    """Find headings that are just a standard item title on a line of its own ("RISK FACTORS")."""
    title_words = {key: _words(title) for key, title in titles.items()}
    headings = []  # (start of line, end of line, key)
    line_start = 0
    for line in text.split("\n"):
        line_end = line_start + len(line)
        words = _words(line)
        if words and len(line) <= MAX_HEADING_LENGTH:
            for key, title in title_words.items():
                is_start_of_long_title = (
                    len(title.split()) >= MIN_WORDS_FOR_TITLE_PREFIX
                    and len(words.split()) >= 3
                    and title.startswith(words + " ")
                )
                if words == title or is_start_of_long_title:
                    headings.append((line_start, line_end, key))
                    break
        line_start = line_end + 1  # +1 for the "\n" that split() removed
    return headings


def split_sections(text: str, form: str) -> list[Section]:
    """Split a filing's text (from html_to_text) into item sections, in document order."""
    if form not in FORMS:
        raise NotImplementedError(f"split_sections supports {', '.join(FORMS)}, not {form!r}")
    item_re, titles, uses_parts = FORMS[form]

    # 1. Walk the text line by line, remembering where each line starts, and collect
    #    every heading line that looks real. For 10-Qs, also track the current PART,
    #    because "Item 1" means different things in Part I and Part II.
    headings = []  # (start of line, end of line, key)
    current_part = None
    last_number = 0  # the previous item's number within the current part (10-Q only)
    line_start = 0
    for line in text.split("\n"):
        line_end = line_start + len(line)
        part_match = _heading_match(PART_RE, line) if uses_parts else None
        item_match = _heading_match(item_re, line, titles)
        if part_match:
            current_part = part_match.group(2).upper()
            last_number = 0
        elif item_match:
            key = item_match.group(2).upper()
            if uses_parts:
                # Items normally count upwards inside a part. If the number drops while we
                # think we are in Part II, the filing has gone back to Part I without a
                # "PART I" line (NFLX's body has none; the table of contents ended in Part II).
                number = int(re.match(r"\d+", key).group())
                if current_part == "II" and number < last_number:
                    current_part = "I"
                last_number = number
                # An item before any PART line can't be keyed, so it is skipped.
                key = f"{current_part}-{key}" if current_part else None
            if key in titles:
                headings.append((line_start, line_end, key))
        line_start = line_end + 1  # +1 for the "\n" that split() removed

    footers = _find_page_footers(text)
    sections = _build_sections(text, headings, titles, form, footers)

    # Annual-report-style 10-Ks (MCD, INTC) have no "Item N" headings in the body: the items
    # are only listed in an index at the end. Their sections are headed by the standard
    # titles instead ("RISK FACTORS"). Try that when the Item headings found next to nothing.
    if form == "10-K" and _real_section_count(sections) < MIN_REAL_SECTIONS:
        by_title = _build_sections(text, _find_title_headings(text, titles), titles, form, footers)
        if _real_section_count(by_title) > _real_section_count(sections):
            return by_title
    return sections


# ---- cross-reference layout detection -----------------------------------

# The items Phase 1 extracts. Only these are checked: Items 10-14 point to the proxy
# statement in nearly every 10-K, so checking them would flag every filing.
CROSS_REFERENCE_KEYS = ["1A", "7", "7A", "I-2"]

# A real Item 7 is tens of thousands of characters. A pointer is a sentence or two.
MAX_CROSS_REFERENCE_CHARS = 2_000

# "appears on pages 46-160", "on page 133", ...
PAGE_REFERENCE_RE = re.compile(r"\bpages?\s+\d+", re.IGNORECASE)

# A table-of-contents line ends with a page number: "Introduction | 4".
TOC_LINE_RE = re.compile(r"\|\s*\d+\s*$")


def _looks_like_table_of_contents(text: str) -> bool:
    """True if the text is mostly lines like "Executive Overview | 5"."""
    lines = [line for line in text.split("\n") if line.strip()]
    toc_lines = [line for line in lines if TOC_LINE_RE.search(line)]
    return len(lines) >= 3 and len(toc_lines) / len(lines) >= 0.7


def find_cross_references(sections: list[Section]) -> list[str]:
    """Return the keys of sections that only point to text elsewhere ("see pages 46-160").

    Some filers (e.g. JPM) put a short 10-K "wrapper" in front of their annual report.
    Items 7, 7A and 8 then say where the real text is instead of containing it.
    A filing with any such section has a non-standard layout.

    JPM's 10-Qs go one step further: the body has no "Item 2" heading at all, so the
    only copy found is the table of contents. That counts as a pointer too.
    """
    return [
        s.key
        for s in sections
        if s.key in CROSS_REFERENCE_KEYS
        and s.char_count <= MAX_CROSS_REFERENCE_CHARS
        and (PAGE_REFERENCE_RE.search(s.text) or _looks_like_table_of_contents(s.text))
    ]

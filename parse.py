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


def _heading_match(regex: re.Pattern, line: str) -> re.Match | None:
    """Match a heading regex against a whole line, rejecting prose-length lines."""
    match = regex.fullmatch(line)
    if match and len(match.group(1)) <= MAX_HEADING_LENGTH:
        return match
    return None


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
    line_start = 0
    for line in text.split("\n"):
        line_end = line_start + len(line)
        part_match = _heading_match(PART_RE, line) if uses_parts else None
        item_match = _heading_match(item_re, line)
        if part_match:
            current_part = part_match.group(2).upper()
        elif item_match:
            key = item_match.group(2).upper()
            if uses_parts:
                # An item before any PART line can't be keyed, so it is skipped.
                key = f"{current_part}-{key}" if current_part else None
            if key in titles:
                headings.append((line_start, line_end, key))
        line_start = line_end + 1  # +1 for the "\n" that split() removed

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
    sections = []
    for key, (_, start, heading_end, end) in sorted(best.items(), key=lambda kv: kv[1][1]):
        body = text[heading_end:end].strip()
        # Skip items with nothing under the heading, e.g. "Item 6. [Reserved]".
        if body:
            sections.append(Section(key, titles[key], body))
    return sections


# ---- cross-reference layout detection -----------------------------------

# The items Phase 1 extracts. Only these are checked: Items 10-14 point to the proxy
# statement in nearly every 10-K, so checking them would flag every filing.
CROSS_REFERENCE_KEYS = ["1A", "7", "7A", "I-2"]

# A real Item 7 is tens of thousands of characters. A pointer is a sentence or two.
MAX_CROSS_REFERENCE_CHARS = 2_000

# "appears on pages 46-160", "on page 133", ...
PAGE_REFERENCE_RE = re.compile(r"\bpages?\s+\d+", re.IGNORECASE)


def find_cross_references(sections: list[Section]) -> list[str]:
    """Return the keys of sections that only point to text elsewhere ("see pages 46-160").

    Some filers (e.g. JPM) put a short 10-K "wrapper" in front of their annual report.
    Items 7, 7A and 8 then say where the real text is instead of containing it.
    A filing with any such section has a non-standard layout.
    """
    return [
        s.key
        for s in sections
        if s.key in CROSS_REFERENCE_KEYS
        and s.char_count <= MAX_CROSS_REFERENCE_CHARS
        and PAGE_REFERENCE_RE.search(s.text)
    ]

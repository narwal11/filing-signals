import re
import warnings

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

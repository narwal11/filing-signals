"""Judge one parsed filing: is a required section missing, is the layout non-standard,
does the SEC metadata disagree with the document?

Used by scripts/miss_report.py (the report) and db.py (the `layout`,
`sec_metadata_mismatch` and `pointer_kind` columns), so the database and RESULTS.md
can never disagree about which filings are non-standard.

Input is one record of data/filings.jsonl: {"filing": {...}, "sections": [...], "item_keys_found": [...]}.
"""

import re

# _looks_like_table_of_contents is reused (not copied) so this module and parse.py can never
# disagree about what a table of contents is.
from parse import Section, _looks_like_table_of_contents, find_cross_references

# form -> {required section key: minimum characters for it to count as a real section}.
REQUIRED = {
    "10-K": {"1A": 5_000, "7": 5_000},
    "10-Q": {"I-2": 2_000},
}
# form -> optional section keys: reported in their own table, never counted as a miss.
# 10-K 7A: some filers fold market risk into MD&A (MCD). 10-Q II-1A: may be left out (XOM).
OPTIONAL = {
    "10-K": ["7A"],
    "10-Q": ["II-1A"],
}
# 8-K: only earnings 8-Ks (SEC metadata lists Item 2.02) are checked. One of these counts as
# evidence that the earnings text was captured: an EX-99.* exhibit, or an Item 2.02/8.01 body,
# with at least this many characters.
EIGHT_K_MIN_CHARS = 1_000
EIGHT_K_BODY_KEYS = ("2.02", "8.01")

FORMS = ["10-K", "10-Q", "8-K"]
# Outcomes for one filing:
OK, MISS, NON_STANDARD = "ok", "miss", "non-standard layout"


def check_filing(record: dict) -> dict | None:
    """Judge one record. Returns None if this filing isn't checked, else a dict with
    outcome, absent (keys), too_short ({key: chars}), optional_absent (keys), mismatch (bool)."""
    filing = record["filing"]
    form = filing["form"]
    if form not in FORMS:
        return None
    sections = {s["key"]: s for s in record["sections"]}
    result = {"outcome": OK, "absent": [], "too_short": {}, "optional_absent": [], "mismatch": False, "pointers": {}}

    if form == "8-K":
        if "2.02" not in filing["items"]:
            return None  # not an earnings 8-K
        candidates = [s for s in record["sections"] if s["source"].startswith("EX-99") or s["key"] in EIGHT_K_BODY_KEYS]
        if not any(s["char_count"] >= EIGHT_K_MIN_CHARS for s in candidates):
            if candidates:
                result["too_short"] = {s["key"]: s["char_count"] for s in candidates}
            else:
                result["absent"] = ["EX-99 or Item 2.02/8.01 text"]
        # The SEC says Item 2.02, but the document has item headings and none is 2.02.
        found = record.get("item_keys_found")
        result["mismatch"] = bool(found) and "2.02" not in found
    else:
        for key, min_chars in REQUIRED[form].items():
            if key not in sections:
                result["absent"].append(key)
            elif sections[key]["char_count"] < min_chars:
                result["too_short"][key] = sections[key]["char_count"]
        result["optional_absent"] = [key for key in OPTIONAL[form] if key not in sections]

    if result["absent"] or result["too_short"]:
        result["outcome"] = MISS
        # A short section that only says "see pages 46-160" is a layout, not a parser miss.
        if result["too_short"] and not result["absent"] and form != "8-K":
            as_sections = [
                Section(s["key"], s["title"], s["text"], s["source"], s.get("heading", "")) for s in record["sections"]
            ]
            by_key = {s.key: s for s in as_sections}
            pointers = set(find_cross_references(as_sections))
            if set(result["too_short"]) <= pointers:
                result["outcome"] = NON_STANDARD
                # key -> (how it points elsewhere, its text), for the layout breakdown
                result["pointers"] = {
                    key: (pointer_kind(by_key[key].body), by_key[key].body) for key in result["too_short"]
                }
    return result


def pointer_kind(text: str) -> str:
    """How a section that find_cross_references already flagged points elsewhere (report labels only):

      "toc"  - only a list of sub-topics with page numbers ("Introduction | 4")
      "page" - names pages: "appears on pages 46-160"
      "name" - names a place without a page: "presented in the Financial Table of Contents"
    """
    if _looks_like_table_of_contents(text):
        return "toc"
    if re.search(r"\bpages?\s+\d+", text, re.IGNORECASE):
        return "page"
    return "name"  # it was flagged, so by elimination it uses a pointer phrase


# What each kind of pointer means, in plain words (see pointer_kind).
LAYOUT_MEANING = {
    "toc": "only a list of sub-topics with page numbers, no body heading",
    "page": "names the pages that hold the text",
    "name": "names where the text is (e.g. a Financial Table of Contents), no page",
}

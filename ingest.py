from edgar import EdgarClient, Filing
from parse import Section, html_to_text, split_sections

# The sections Phase 1 keeps for each form. Everything else is dropped.
RELEVANT_KEYS = {
    "10-K": {"1A", "7", "7A"},
    "10-Q": {"I-2", "II-1A"},
    "8-K": {"2.02", "7.01", "8.01"},
}

# Exhibit files we can turn into text. Images, PDFs and XBRL files are skipped.
TEXT_EXTENSIONS = (".htm", ".html", ".txt")


def _exhibit_section(client: EdgarClient, doc_type: str, url: str) -> Section | None:
    """Download one exhibit and wrap it as a Section, or return None if it has no text."""
    raw = client.get(url)
    if url.lower().endswith(".txt"):
        # Plain text already has meaningful line breaks; html_to_text would turn them into spaces.
        text = raw.decode("utf-8", errors="replace").strip()
    else:
        text = html_to_text(raw)
    if not text:
        return None
    return Section(key=doc_type, title=doc_type, text=text, source=doc_type)


def process(client: EdgarClient, filing: Filing) -> list[Section]:
    """Turn one filing into the sections we keep.

    For earnings 8-Ks (Item 2.02), the numbers are in the press release attached as
    Exhibit 99.x, not in the 8-K itself, so those exhibits are added as extra sections.
    """
    text = html_to_text(client.download(filing))
    sections = [s for s in split_sections(text, filing.form) if s.key in RELEVANT_KEYS[filing.form]]

    if filing.form == "8-K" and "2.02" in filing.items:
        for doc_type, url in client.exhibits(filing):
            if doc_type.startswith("EX-99") and url.lower().endswith(TEXT_EXTENSIONS):
                section = _exhibit_section(client, doc_type, url)
                if section is not None:
                    sections.append(section)
    return sections

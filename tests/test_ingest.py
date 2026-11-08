import os
from dataclasses import replace
from datetime import date

import httpx
import pytest

# edgar.py reads this at import time; give it a dummy value so tests never need .env
os.environ.setdefault("SEC_USER_AGENT", "test-agent test@example.com")

import edgar
import ingest

FOLDER = "/Archives/edgar/data/320193/000032019325000077/"

EARNINGS_8K = edgar.Filing(
    cik=320193,
    ticker="AAPL",
    company="Apple Inc.",
    accession="0000320193-25-000077",
    form="8-K",
    filed_date=date(2025, 10, 30),
    accepted_at=None,
    report_date=date(2025, 10, 30),
    items=("2.02", "9.01"),
    primary_document="aapl-8k.htm",
)

EIGHT_K_HTML = """<html><body>
<p>Item 2.02 Results of Operations and Financial Condition.</p>
<p>On October 30, 2025, Apple Inc. issued a press release regarding its financial results.</p>
<p>Item 9.01 Financial Statements and Exhibits.</p>
<p>99.1 Press release.</p>
</body></html>"""

INDEX_HTML = f"""<table class="tableFile">
<tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
<tr><td>1</td><td>8-K</td><td><a href="/ix?doc={FOLDER}aapl-8k.htm">aapl-8k.htm</a></td><td>8-K</td><td>1</td></tr>
<tr><td>2</td><td>PR</td><td><a href="{FOLDER}ex991.htm">ex991.htm</a></td><td>EX-99.1</td><td>1</td></tr>
<tr><td>3</td><td>CFO</td><td><a href="{FOLDER}ex992.txt">ex992.txt</a></td><td>EX-99.2</td><td>1</td></tr>
<tr><td>4</td><td>Logo</td><td><a href="{FOLDER}logo.jpg">logo.jpg</a></td><td>GRAPHIC</td><td>1</td></tr>
<tr><td>5</td><td>XBRL</td><td><a href="{FOLDER}aapl-20251030.xsd">aapl-20251030.xsd</a></td><td>EX-101.SCH</td><td>1</td></tr>
</table>"""

TEN_K_HTML = """<html><body>
<p>Item 1. Business</p><p>We make phones.</p>
<p>Item 1A. Risk Factors</p><p>Many risks.</p>
<p>Item 7. Management's Discussion and Analysis</p><p>Sales grew.</p>
<p>Item 7A. Quantitative and Qualitative Disclosures About Market Risk</p><p>Rates.</p>
<p>Item 8. Financial Statements and Supplementary Data</p><p>Balance sheet.</p>
</body></html>"""

ROUTES = {
    FOLDER + "aapl-8k.htm": EIGHT_K_HTML,
    FOLDER + "0000320193-25-000077-index.htm": INDEX_HTML,
    FOLDER + "ex991.htm": "<html><body><p>Apple reports fourth quarter results</p><p>Revenue $102.5 billion</p></body></html>",
    FOLDER + "ex992.txt": "CFO commentary\nGross margin was 47.2%.",
    FOLDER + "aapl-10k.htm": TEN_K_HTML,
}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(edgar.time, "sleep", lambda seconds: None)


def make_client(tmp_path):
    """Return (EdgarClient, paths) backed by ROUTES; `paths` records every request."""
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path in ROUTES:
            return httpx.Response(200, text=ROUTES[request.url.path])
        return httpx.Response(404)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    return edgar.EdgarClient(http, cache_dir=tmp_path), paths


def test_earnings_8k_adds_ex99_exhibits(tmp_path):
    client, paths = make_client(tmp_path)
    sections = ingest.process(client, EARNINGS_8K)

    assert [(s.key, s.source) for s in sections] == [
        ("2.02", "primary"),
        ("EX-99.1", "EX-99.1"),
        ("EX-99.2", "EX-99.2"),
    ]
    by_key = {s.key: s for s in sections}
    assert by_key["EX-99.1"].text == "Apple reports fourth quarter results\n\nRevenue $102.5 billion"
    # .txt exhibits keep their line breaks
    assert by_key["EX-99.2"].text == "CFO commentary\nGross margin was 47.2%."
    # Item 9.01 is not relevant; images and XBRL files are never downloaded.
    assert "9.01" not in by_key
    assert FOLDER + "logo.jpg" not in paths
    assert FOLDER + "aapl-20251030.xsd" not in paths


def test_8k_without_2_02_skips_exhibits(tmp_path):
    client, paths = make_client(tmp_path)
    other_8k = replace(EARNINGS_8K, items=("5.02",))
    ingest.process(client, other_8k)
    assert FOLDER + "0000320193-25-000077-index.htm" not in paths


def test_10k_keeps_only_relevant_items(tmp_path):
    client, _ = make_client(tmp_path)
    ten_k = replace(EARNINGS_8K, form="10-K", items=(), primary_document="aapl-10k.htm")
    sections = ingest.process(client, ten_k)
    assert [s.key for s in sections] == ["1A", "7", "7A"]


# ---- the command-line tool (run, read_tickers, resuming) -------------------

import json


def folder(accession):
    return f"/Archives/edgar/data/320193/{accession.replace('-', '')}/"


def submissions_json():
    rows = [  # (accession, form, filed, items, document)
        ("0000320193-25-000066", "10-K", "2025-10-31", "", "aapl-10k.htm"),
        ("0000320193-25-000077", "8-K", "2025-10-30", "2.02,9.01", "aapl-8k.htm"),
        ("0000320193-25-000050", "8-K", "2025-08-01", "5.02", "other.htm"),  # not earnings
        ("0000320193-25-000040", "8-K", "2025-07-31", "2.02", "broken.htm"),  # 404s
    ]
    recent = {
        "accessionNumber": [r[0] for r in rows],
        "form": [r[1] for r in rows],
        "filingDate": [r[2] for r in rows],
        "acceptanceDateTime": [""] * len(rows),
        "reportDate": [""] * len(rows),
        "items": [r[3] for r in rows],
        "primaryDocument": [r[4] for r in rows],
    }
    return json.dumps({"name": "Apple Inc.", "filings": {"recent": recent, "files": []}})


ROUTES["/files/company_tickers.json"] = json.dumps({"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple"}})
ROUTES["/submissions/CIK0000320193.json"] = submissions_json()
ROUTES[folder("0000320193-25-000066") + "aapl-10k.htm"] = TEN_K_HTML
ROUTES[folder("0000320193-25-000050") + "other.htm"] = "<p>Item 5.02 Departure of Directors</p><p>Text.</p>"


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_read_tickers_handles_comments_blanks_and_duplicates(tmp_path):
    tickers_file = tmp_path / "t.txt"
    tickers_file.write_text("# tech\naapl\n\nMSFT  # the big one\n  # nothing\nAAPL\n", encoding="utf-8")
    assert ingest.read_tickers(["nvda", "MSFT"], tickers_file) == ["NVDA", "MSFT", "AAPL"]


def test_run_writes_one_line_per_filing_and_survives_a_failure(tmp_path):
    client, _ = make_client(tmp_path / "cache")
    out = tmp_path / "filings.jsonl"
    stats = ingest.run(client, ["AAPL"], ["10-K", "8-K"], None, out)

    assert stats.ok == {"10-K": 1, "8-K": 1}
    assert stats.failed == 1  # the 8-K whose document 404s; the run still finished
    records = read_lines(out)
    assert len(records) == 2  # the non-earnings 8-K was filtered out; the failure wasn't written
    eight_k = next(r for r in records if r["filing"]["form"] == "8-K")
    assert eight_k["filing"]["accession"] == "0000320193-25-000077"
    assert eight_k["filing"]["items"] == ["2.02", "9.01"]
    assert eight_k["filing"]["filed_date"] == "2025-10-30"
    assert eight_k["filing"]["primary_url"].endswith("/aapl-8k.htm")
    assert [s["key"] for s in eight_k["sections"]] == ["2.02", "EX-99.1", "EX-99.2"]
    assert eight_k["sections"][0]["char_count"] == len(eight_k["sections"][0]["text"])


def test_rerun_skips_finished_filings_and_retries_failures(tmp_path):
    client, _ = make_client(tmp_path / "cache")
    out = tmp_path / "filings.jsonl"
    ingest.run(client, ["AAPL"], ["10-K", "8-K"], None, out)
    stats = ingest.run(client, ["AAPL"], ["10-K", "8-K"], None, out)

    assert stats.skipped == 2
    assert stats.failed == 1  # retried, and failed again
    assert len(read_lines(out)) == 2  # nothing written twice


def test_all_8ks_includes_non_earnings_and_counts_no_sections(tmp_path):
    client, _ = make_client(tmp_path / "cache")
    out = tmp_path / "filings.jsonl"
    stats = ingest.run(client, ["AAPL"], ["8-K"], None, out, earnings_only=False)

    assert stats.no_sections == 1  # the 5.02 8-K has no wanted section...
    other = next(r for r in read_lines(out) if r["filing"]["accession"] == "0000320193-25-000050")
    assert other["sections"] == []  # ...but it is still recorded, so miss_report can see it


def test_max_per_ticker_keeps_the_most_recent_of_each_form(tmp_path):
    client, _ = make_client(tmp_path / "cache")
    out = tmp_path / "filings.jsonl"
    ingest.run(client, ["AAPL"], ["8-K"], None, out, max_per_ticker=1)
    assert [r["filing"]["accession"] for r in read_lines(out)] == ["0000320193-25-000077"]


def test_truncated_last_line_is_ignored_and_not_glued_to_the_next_record(tmp_path):
    client, _ = make_client(tmp_path / "cache")
    out = tmp_path / "filings.jsonl"
    out.write_text('{"filing": {"accession": "0000320193-25-0000', encoding="utf-8")  # crash mid-write
    ingest.run(client, ["AAPL"], ["10-K"], None, out)

    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith('{"filing": {"accession": "0000320193-25-0000')
    assert json.loads(lines[1])["filing"]["form"] == "10-K"


def test_record_lists_every_item_heading_found_not_just_the_kept_ones(tmp_path):
    client, _ = make_client(tmp_path / "cache")
    out = tmp_path / "filings.jsonl"
    ingest.run(client, ["AAPL"], ["10-K"], None, out)
    record = read_lines(out)[0]
    assert [s["key"] for s in record["sections"]] == ["1A", "7", "7A"]  # kept
    assert record["item_keys_found"] == ["1", "1A", "7", "7A", "8"]  # all found


# ---- round 3: the SEC packaging line at the top of every exhibit --------------------

def test_sec_exhibit_header_line_is_removed():
    # Real first lines of an AAPL press release exhibit.
    text = "EX-99.1 2 a8-kex991q1202112262020.htm EX-99.1\nExhibit 99.1\nApple Reports First Quarter Results"
    assert ingest._strip_exhibit_header(text) == "Exhibit 99.1\nApple Reports First Quarter Results"


def test_exhibit_without_a_header_line_is_unchanged():
    text = "Exhibit 99.1\nApple Reports First Quarter Results"
    assert ingest._strip_exhibit_header(text) == text

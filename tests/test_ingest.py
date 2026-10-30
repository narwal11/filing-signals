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

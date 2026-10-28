import os
from datetime import date, datetime

import httpx
import pytest

# edgar.py reads this at import time; give it a dummy value so tests never need .env
os.environ.setdefault("SEC_USER_AGENT", "test-agent test@example.com")

import edgar

FAKE_TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
    "2": {"cik_str": 1067983, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY INC"},
}

RECENT = {
    "accessionNumber": ["0000320193-22-000108", "0000320193-22-000050", "0000320193-21-000105"],
    "form": ["10-K", "8-K", "10-K"],
    "filingDate": ["2022-10-28", "2022-04-28", "2021-10-29"],
    "acceptanceDateTime": [
        "2022-10-27T18:01:14.000Z",
        "2022-04-28T16:30:00.000Z",
        "2021-10-28T18:04:28.000Z",
    ],
    "reportDate": ["2022-09-24", "", "2021-09-25"],
    "items": ["", "2.02,9.01", ""],
    "primaryDocument": ["aapl-20220924.htm", "aapl-8k.htm", "aapl-20210925.htm"],
}
OLD_PAGE = {
    "accessionNumber": ["0001193125-10-238044"],
    "form": ["10-K"],
    "filingDate": ["2010-10-27"],
    "acceptanceDateTime": ["2010-10-27T17:00:00.000Z"],
    "reportDate": ["2010-09-25"],
    "items": [""],
    "primaryDocument": ["d10k.htm"],
}

DOC_PATH = "/Archives/edgar/data/320193/000032019322000108/aapl-20220924.htm"
DOC_URL = "https://www.sec.gov" + DOC_PATH


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    """Replace time.sleep so tests run fast. Returns the list of requested delays."""
    delays = []
    monkeypatch.setattr(edgar.time, "sleep", delays.append)
    return delays


def sec_handler(request: httpx.Request) -> httpx.Response:
    """A tiny fake SEC: tickers, Apple's submissions, one older page, one document."""
    path = request.url.path
    if path == "/files/company_tickers.json":
        return httpx.Response(200, json=FAKE_TICKERS)
    if path == "/submissions/CIK0000320193.json":
        return httpx.Response(
            200,
            json={
                "name": "Apple Inc.",
                "filings": {
                    "recent": RECENT,
                    "files": [
                        {
                            "name": "CIK0000320193-submissions-001.json",
                            "filingFrom": "1994-01-26",
                            "filingTo": "2015-12-31",
                        }
                    ],
                },
            },
        )
    if path == "/submissions/CIK0000320193-submissions-001.json":
        return httpx.Response(200, json=OLD_PAGE)
    if path == DOC_PATH:
        return httpx.Response(200, content=b"<html>10-K body</html>")
    return httpx.Response(404)


def make_edgar(tmp_path, handler=sec_handler):
    """Return (EdgarClient, paths). `paths` records the URL path of every network request."""
    paths = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return handler(request)

    http = httpx.Client(transport=httpx.MockTransport(recording_handler))
    return edgar.EdgarClient(http, cache_dir=tmp_path), paths


# ---- ticker_to_cik --------------------------------------------------------


def test_apple_lookup(tmp_path):
    client, _ = make_edgar(tmp_path)
    assert client.ticker_to_cik("AAPL") == 320193


def test_lowercase_lookup(tmp_path):
    client, _ = make_edgar(tmp_path)
    assert client.ticker_to_cik("aapl") == 320193


def test_dot_ticker_converted_to_dash(tmp_path):
    client, _ = make_edgar(tmp_path)
    assert client.ticker_to_cik("BRK.B") == 1067983


def test_unknown_ticker_raises(tmp_path):
    client, _ = make_edgar(tmp_path)
    with pytest.raises(ValueError, match="Unknown ticker"):
        client.ticker_to_cik("NOPE")


def test_tickers_file_fetched_only_once(tmp_path):
    client, paths = make_edgar(tmp_path)
    client.ticker_to_cik("AAPL")
    client.ticker_to_cik("MSFT")
    client.ticker_to_cik("aapl")
    assert paths == ["/files/company_tickers.json"]


# ---- list_filings ---------------------------------------------------------


def test_form_filtering(tmp_path):
    client, _ = make_edgar(tmp_path)
    result = client.list_filings("AAPL", ["8-K"])
    assert [f.form for f in result] == ["8-K"]


def test_date_filtering_and_sorting(tmp_path):
    client, _ = make_edgar(tmp_path)
    result = client.list_filings(
        "AAPL", ["10-K", "8-K"], since=date(2021, 11, 1), until=date(2022, 10, 27)
    )
    assert [f.filed_date for f in result] == [date(2022, 4, 28)]

    everything = client.list_filings("AAPL", ["10-K"])
    assert [f.filed_date for f in everything] == [
        date(2010, 10, 27),
        date(2021, 10, 29),
        date(2022, 10, 28),
    ]


def test_old_page_skipped_when_it_ends_before_since(tmp_path):
    client, paths = make_edgar(tmp_path)
    client.list_filings("AAPL", ["10-K"], since=date(2021, 1, 1))
    assert "/submissions/CIK0000320193-submissions-001.json" not in paths


def test_primary_url(tmp_path):
    client, _ = make_edgar(tmp_path)
    filing = client.list_filings("AAPL", ["10-K"], since=date(2022, 1, 1))[0]
    assert filing.primary_url == DOC_URL


def test_items_and_dates_parsing(tmp_path):
    client, _ = make_edgar(tmp_path)
    eight_k = client.list_filings("AAPL", ["8-K"])[0]
    assert eight_k.items == ("2.02", "9.01")
    assert eight_k.report_date is None
    assert eight_k.accepted_at == datetime.fromisoformat("2022-04-28T16:30:00+00:00")
    assert eight_k.company == "Apple Inc."

    ten_k = client.list_filings("AAPL", ["10-K"], since=date(2022, 1, 1))[0]
    assert ten_k.items == ()


# ---- caching --------------------------------------------------------------


def test_second_get_uses_disk_cache(tmp_path):
    client, paths = make_edgar(tmp_path)
    first = client.get(DOC_URL)
    second = client.get(DOC_URL)
    assert first == second == b"<html>10-K body</html>"
    assert paths == [DOC_PATH]  # only the first call hit the network
    assert (tmp_path / DOC_PATH.lstrip("/")).exists()


def test_submissions_are_not_cached(tmp_path):
    client, paths = make_edgar(tmp_path)
    client.list_filings("AAPL", ["10-K"], since=date(2021, 1, 1))
    client.list_filings("AAPL", ["10-K"], since=date(2021, 1, 1))
    assert paths.count("/submissions/CIK0000320193.json") == 2
    assert not (tmp_path / "submissions").exists()


def test_download_returns_document_bytes(tmp_path):
    client, paths = make_edgar(tmp_path)
    filing = client.list_filings("AAPL", ["10-K"], since=date(2022, 1, 1))[0]
    assert client.download(filing) == b"<html>10-K body</html>"
    assert client.download(filing) == b"<html>10-K body</html>"
    assert paths.count(DOC_PATH) == 1


def test_cache_path_cannot_escape_cache_dir(tmp_path):
    client, _ = make_edgar(tmp_path)
    with pytest.raises(ValueError, match="Unsafe cache path"):
        client.get("https://www.sec.gov/Archives/../../outside.htm")


# ---- retries --------------------------------------------------------------


def test_429_then_200_succeeds_after_retry(tmp_path, sleeps):
    responses = [httpx.Response(429), httpx.Response(200, content=b"ok")]
    client, paths = make_edgar(tmp_path, handler=lambda request: responses.pop(0))
    assert client.get(DOC_URL) == b"ok"
    assert len(paths) == 2
    assert 1 in sleeps  # waited 1 second before the retry


def test_gives_up_after_max_retries(tmp_path, sleeps):
    client, paths = make_edgar(tmp_path, handler=lambda request: httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        client.get(DOC_URL)
    assert len(paths) == 6  # 1 try + 5 retries
    assert [s for s in sleeps if s >= 1] == [1, 2, 4, 8, 16]


def test_connection_error_is_retried(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, content=b"ok")

    client, _ = make_edgar(tmp_path, handler=handler)
    assert client.get(DOC_URL) == b"ok"
    assert len(calls) == 2


def test_404_is_not_retried(tmp_path):
    client, paths = make_edgar(tmp_path, handler=lambda request: httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        client.get(DOC_URL)
    assert len(paths) == 1


# ---- rate limiting --------------------------------------------------------


def test_rate_limit_applies_to_network_requests_only(tmp_path, sleeps, monkeypatch):
    monkeypatch.setattr(edgar.time, "monotonic", lambda: 100.0)  # time stands still
    client, _ = make_edgar(tmp_path)

    client.get(DOC_URL)  # 1st request: nothing to wait for
    assert sleeps == []

    client.get(DOC_URL)  # cache hit: still no waiting
    assert sleeps == []

    client.get(DOC_URL, cache=False)  # 2nd real request, 0s after the first
    assert sleeps == [pytest.approx(1 / 8)]

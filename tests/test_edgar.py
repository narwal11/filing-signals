import os

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


@pytest.fixture(autouse=True)
def reset_cache():
    """Each test starts with an empty cache so tests don't affect each other."""
    edgar._ticker_to_cik_map = None


def make_client():
    """Return (client, calls). `calls` records every request the mock receives."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=FAKE_TICKERS)

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_apple_lookup():
    client, _ = make_client()
    assert edgar.ticker_to_cik("AAPL", client) == 320193


def test_lowercase_lookup():
    client, _ = make_client()
    assert edgar.ticker_to_cik("aapl", client) == 320193


def test_dot_ticker_converted_to_dash():
    client, _ = make_client()
    assert edgar.ticker_to_cik("BRK.B", client) == 1067983


def test_unknown_ticker_raises():
    client, _ = make_client()
    with pytest.raises(ValueError, match="Unknown ticker"):
        edgar.ticker_to_cik("NOPE", client)


def test_file_fetched_only_once():
    client, calls = make_client()
    edgar.ticker_to_cik("AAPL", client)
    edgar.ticker_to_cik("MSFT", client)
    edgar.ticker_to_cik("aapl", client)
    assert len(calls) == 1


# ---- list_filings -------------------------------------------------------

from datetime import date, datetime

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


def make_filings_client():
    """Mock SEC that serves tickers, a submissions file, and one older page.

    Returns (client, paths) where paths lists every URL path requested.
    """
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/files/company_tickers.json":
            return httpx.Response(200, json=FAKE_TICKERS)
        if request.url.path == "/submissions/CIK0000320193.json":
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
        if request.url.path == "/submissions/CIK0000320193-submissions-001.json":
            return httpx.Response(200, json=OLD_PAGE)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler)), paths


def test_form_filtering():
    client, _ = make_filings_client()
    result = edgar.list_filings("AAPL", ["8-K"], client=client)
    assert [f.form for f in result] == ["8-K"]


def test_date_filtering_and_sorting():
    client, _ = make_filings_client()
    result = edgar.list_filings(
        "AAPL", ["10-K", "8-K"], since=date(2021, 11, 1), until=date(2022, 10, 27), client=client
    )
    assert [f.filed_date for f in result] == [date(2022, 4, 28)]

    everything = edgar.list_filings("AAPL", ["10-K"], client=client)
    assert [f.filed_date for f in everything] == [
        date(2010, 10, 27),
        date(2021, 10, 29),
        date(2022, 10, 28),
    ]


def test_old_page_skipped_when_it_ends_before_since():
    client, paths = make_filings_client()
    edgar.list_filings("AAPL", ["10-K"], since=date(2021, 1, 1), client=client)
    assert "/submissions/CIK0000320193-submissions-001.json" not in paths


def test_primary_url():
    client, _ = make_filings_client()
    filing = edgar.list_filings("AAPL", ["10-K"], since=date(2022, 1, 1), client=client)[0]
    assert filing.primary_url == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019322000108/aapl-20220924.htm"
    )


def test_items_and_dates_parsing():
    client, _ = make_filings_client()
    eight_k = edgar.list_filings("AAPL", ["8-K"], client=client)[0]
    assert eight_k.items == ("2.02", "9.01")
    assert eight_k.report_date is None
    assert eight_k.accepted_at == datetime.fromisoformat("2022-04-28T16:30:00+00:00")
    assert eight_k.company == "Apple Inc."

    ten_k = edgar.list_filings("AAPL", ["10-K"], since=date(2022, 1, 1), client=client)[0]
    assert ten_k.items == ()

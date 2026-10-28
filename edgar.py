import json
import os
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

load_dotenv()  # reads your .env file
HEADERS = {"User-Agent": os.environ["SEC_USER_AGENT"]}

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/{name}"

MAX_REQUESTS_PER_SECOND = 8  # SEC limit is 10; we stay under it
RETRY_STATUSES = {403, 429, 500, 502, 503, 504}
MAX_RETRIES = 5  # 1 first try + 5 retries = 6 attempts at most


@dataclass(frozen=True)
class Filing:
    cik: int
    ticker: str
    company: str
    accession: str  # e.g. "0000320193-21-000105" (with dashes)
    form: str  # e.g. "10-K"
    filed_date: date
    accepted_at: datetime | None
    report_date: date | None
    items: tuple[str, ...]  # 8-K item numbers, e.g. ("2.02", "9.01")
    primary_document: str

    @property
    def primary_url(self) -> str:
        # In the Archives path, the accession number has its dashes removed.
        accession_no_dashes = self.accession.replace("-", "")
        return (
            f"https://www.sec.gov/Archives/edgar/data/"
            f"{self.cik}/{accession_no_dashes}/{self.primary_document}"
        )


def _parse_filings(columns: dict, cik: int, ticker: str, company: str) -> list[Filing]:
    """Turn one columnar block of the submissions JSON into Filing objects.

    The SEC stores filings as parallel lists, one list per field:
        {"accessionNumber": ["A1", "A2"], "form": ["10-K", "8-K"], ...}
    The value at index 0 of every list describes filing #1, index 1 describes
    filing #2, and so on. zip() walks all the lists in lockstep, so each loop
    pass hands us one filing's worth of values.
    """
    filings = []
    for accession, form, filed, accepted, report, items, document in zip(
        columns["accessionNumber"],
        columns["form"],
        columns["filingDate"],
        columns["acceptanceDateTime"],
        columns["reportDate"],
        columns["items"],
        columns["primaryDocument"],
    ):
        filings.append(
            Filing(
                cik=cik,
                ticker=ticker,
                company=company,
                accession=accession,
                form=form,
                filed_date=date.fromisoformat(filed),
                # Missing values come through as empty strings.
                accepted_at=datetime.fromisoformat(accepted) if accepted else None,
                report_date=date.fromisoformat(report) if report else None,
                # "2.02,9.01" -> ("2.02", "9.01"); "" -> ()
                items=tuple(i.strip() for i in items.split(",") if i.strip()),
                primary_document=document,
            )
        )
    return filings


class EdgarClient:
    """Talks to the SEC. Every request goes through get(), which handles
    the disk cache, rate limit, and retries in one place."""

    def __init__(self, client: httpx.Client | None = None, cache_dir: Path = Path("data/cache")):
        # `client` and `cache_dir` are parameters so tests can pass in a mocked
        # client and a temporary folder.
        self._http = client or httpx.Client(headers=HEADERS, timeout=30)
        self._cache_dir = cache_dir
        self._last_request = 0.0  # time.monotonic() of the last real request
        self._ticker_map = None  # {ticker: cik}, filled on first lookup

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    # ---- low-level network access ------------------------------------

    def _cache_path(self, url: str) -> Path:
        """Map a URL to a file, e.g. https://www.sec.gov/Archives/x.htm -> <cache_dir>/Archives/x.htm"""
        path = (self._cache_dir / urlparse(url).path.lstrip("/")).resolve()
        # The file name comes from SEC data, so make sure it can't escape the cache folder.
        if not path.is_relative_to(self._cache_dir.resolve()):
            raise ValueError(f"Unsafe cache path for {url}")
        return path

    def get(self, url: str, cache: bool = True) -> bytes:
        """Return the response body for `url`, using the disk cache if `cache` is True."""
        if cache:
            path = self._cache_path(url)
            if path.exists():
                return path.read_bytes()  # cache hit: no network, no rate limit

        body = self._fetch(url)

        if cache:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        return body

    def _wait_for_slot(self):
        """Sleep if needed so real requests are at least 1/8 second apart."""
        min_gap = 1 / MAX_REQUESTS_PER_SECOND
        wait = min_gap - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _fetch(self, url: str) -> bytes:
        """Download `url`, retrying temporary failures with exponential backoff."""
        for attempt in range(MAX_RETRIES + 1):
            last_attempt = attempt == MAX_RETRIES
            self._wait_for_slot()
            try:
                response = self._http.get(url)
            except httpx.TransportError:  # connection error, timeout, ...
                if last_attempt:
                    raise
            else:
                if response.status_code not in RETRY_STATUSES:
                    response.raise_for_status()  # e.g. 404: retrying won't help
                    return response.content
                if last_attempt:
                    response.raise_for_status()
            # Wait 1s, 2s, 4s, 8s, 16s: the delay doubles after every failure.
            time.sleep(2**attempt)

    def _get_json(self, url: str) -> dict:
        # Submissions change daily, so they are never cached on disk.
        return json.loads(self.get(url, cache=False))

    # ---- SEC-specific helpers ----------------------------------------

    def _load_ticker_map(self) -> dict[str, int]:
        """Download the SEC tickers file once and keep it as a {ticker: cik} dict."""
        if self._ticker_map is None:
            # The file looks like {"0": {"cik_str": 320193, "ticker": "AAPL", ...}, "1": {...}}
            data = self._get_json(TICKERS_URL)
            self._ticker_map = {
                entry["ticker"].upper(): int(entry["cik_str"]) for entry in data.values()
            }
        return self._ticker_map

    def ticker_to_cik(self, ticker: str) -> int:
        """Return the SEC CIK number for a stock ticker, e.g. 'AAPL' -> 320193."""
        # The SEC writes share classes with a dash ("BRK-B"), people write "BRK.B".
        normalized = ticker.strip().upper().replace(".", "-")
        ticker_map = self._load_ticker_map()
        if normalized not in ticker_map:
            raise ValueError(f"Unknown ticker: {ticker!r}")
        return ticker_map[normalized]

    def list_filings(
        self,
        ticker: str,
        forms: list[str],
        since: date | None = None,
        until: date | None = None,
    ) -> list[Filing]:
        """Return filings of the given form types, oldest first.

        `since` and `until` are inclusive bounds on the filing date.
        """
        cik = self.ticker_to_cik(ticker)
        data = self._get_json(SUBMISSIONS_URL.format(name=f"CIK{cik:010d}.json"))
        company = data["name"]
        clean_ticker = ticker.strip().upper()

        # Recent filings are inline; older ones live in extra pages listed in "files".
        all_filings = _parse_filings(data["filings"]["recent"], cik, clean_ticker, company)
        for page in data["filings"]["files"]:
            # filingTo is the newest filing in the page. If that is before `since`,
            # nothing in the page can match, so don't download it.
            if since is not None and date.fromisoformat(page["filingTo"]) < since:
                continue
            page_data = self._get_json(SUBMISSIONS_URL.format(name=page["name"]))
            all_filings += _parse_filings(page_data, cik, clean_ticker, company)

        wanted = set(forms)
        results = [
            f
            for f in all_filings
            if f.form in wanted
            and (since is None or f.filed_date >= since)
            and (until is None or f.filed_date <= until)
        ]
        results.sort(key=lambda f: f.filed_date)
        return results

    def download(self, filing: Filing) -> bytes:
        """Return the bytes of the filing's primary document (cached on disk)."""
        return self.get(filing.primary_url)


if __name__ == "__main__":
    with EdgarClient() as edgar:
        latest = edgar.list_filings("AAPL", ["10-K"], since=date(2021, 1, 1))[-1]
        data = edgar.download(latest)
        print(f"{latest.form} filed {latest.filed_date}: {len(data):,} bytes")

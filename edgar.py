import os
from dataclasses import dataclass
from datetime import date, datetime

import httpx
from dotenv import load_dotenv

load_dotenv()  # reads your .env file
HEADERS = {"User-Agent": os.environ["SEC_USER_AGENT"]}

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# {ticker: cik}. None until the first lookup downloads the file.
_ticker_to_cik_map = None


def _load_ticker_map(client: httpx.Client | None = None) -> dict[str, int]:
    """Download the SEC tickers file once and cache it as a {ticker: cik} dict."""
    global _ticker_to_cik_map
    if _ticker_to_cik_map is None:
        if client is None:
            client = httpx.Client(headers=HEADERS)
        response = client.get(TICKERS_URL, headers=HEADERS)
        response.raise_for_status()
        # The file looks like {"0": {"cik_str": 320193, "ticker": "AAPL", ...}, "1": {...}}
        _ticker_to_cik_map = {
            entry["ticker"].upper(): int(entry["cik_str"])
            for entry in response.json().values()
        }
    return _ticker_to_cik_map


def ticker_to_cik(ticker: str, client: httpx.Client | None = None) -> int:
    """Return the SEC CIK number for a stock ticker, e.g. 'AAPL' -> 320193.

    `client` is optional and only exists so tests can pass in a mocked client.
    """
    # The SEC writes share classes with a dash ("BRK-B"), people write "BRK.B".
    normalized = ticker.strip().upper().replace(".", "-")
    ticker_map = _load_ticker_map(client)
    if normalized not in ticker_map:
        raise ValueError(f"Unknown ticker: {ticker!r}")
    return ticker_map[normalized]


SUBMISSIONS_URL = "https://data.sec.gov/submissions/{name}"


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


def _get_json(url: str, client: httpx.Client | None) -> dict:
    if client is None:
        client = httpx.Client(headers=HEADERS)
    response = client.get(url, headers=HEADERS)
    response.raise_for_status()
    return response.json()


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


def list_filings(
    ticker: str,
    forms: list[str],
    since: date | None = None,
    until: date | None = None,
    client: httpx.Client | None = None,
) -> list[Filing]:
    """Return filings of the given form types, oldest first.

    `since` and `until` are inclusive bounds on the filing date.
    """
    cik = ticker_to_cik(ticker, client)
    data = _get_json(SUBMISSIONS_URL.format(name=f"CIK{cik:010d}.json"), client)
    company = data["name"]
    clean_ticker = ticker.strip().upper()

    # Recent filings are inline; older ones live in extra pages listed in "files".
    all_filings = _parse_filings(data["filings"]["recent"], cik, clean_ticker, company)
    for page in data["filings"]["files"]:
        # filingTo is the newest filing in the page. If that is before `since`,
        # nothing in the page can match, so don't download it.
        if since is not None and date.fromisoformat(page["filingTo"]) < since:
            continue
        page_data = _get_json(SUBMISSIONS_URL.format(name=page["name"]), client)
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


if __name__ == "__main__":
    for filing in list_filings("AAPL", ["10-K"], since=date(2021, 1, 1)):
        print(filing.filed_date, filing.accession, filing.primary_url)

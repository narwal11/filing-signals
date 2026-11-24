# filing-signals

An LLM pipeline that extracts structured earnings signals from SEC filings (10-K, 10-Q and
earnings 8-K), measures how accurate the extraction is against hand-labeled filings, and
backtests the signals with an event study.

**Status: Phase 1 complete** (download, section extraction, Postgres storage). Phases 2-5 have not started.

## Results (round 3)

Dataset: 25 large-cap tickers, filings since 2021-01-01: 143 10-Ks, 430 10-Qs, 587 earnings 8-Ks.

| Form | Usable coverage | Miss rate |
|------|-----------------|-----------|
| 10-K | 91.6% (131/143) | 0.0% (0/143) |
| 10-Q | 91.6% (394/430) | 0.0% (0/430) |
| 8-K  | 100.0% (587/587) | 0.0% (0/587) |

*Usable coverage* = every expected section found. *Miss rate* = a required section is absent
or too short. The 48 filings not counted as usable (JPM, INTC, CVX) only contain a pointer to
their MD&A; they are reported separately, not hidden in the miss rate (see
[Known limitations](#known-limitations)).

**Spot check** (a random sample of sections, start and end verified by hand):

| Sample | Size | Correct | 95% Wilson interval |
|--------|------|---------|---------------------|
| A (before fixes) | 20 filings, 47 sections | 91.5% (43/47) | 80.1% to 96.6% |
| B (after fixes, fresh sample) | 20 filings, 47 sections | 100.0% (47/47) | 92.4% to 100.0% |

Only sample B is a fair "after" number: sample A was used to find the bugs that were then fixed.
Sections from one filing are not independent, so the intervals are somewhat optimistic.
Details and root causes: [RESULTS.md](RESULTS.md).

## Architecture

```mermaid
flowchart TD
    sec["SEC EDGAR<br/>submissions JSON + Archives"]
    client["EdgarClient (edgar.py)<br/>disk cache, rate limit, retries"]
    text["html_to_text (parse.py)"]
    split["split_sections (parse.py)"]
    jsonl[("data/filings.jsonl<br/>written by ingest.py")]
    classify["classify (classify.py)<br/>layout and metadata checks"]
    report["scripts/miss_report.py<br/>miss rates"]
    db["db.py<br/>idempotent upserts"]
    pg[("Postgres (Supabase)")]

    sec --> client --> text --> split --> jsonl
    jsonl --> classify
    classify --> report
    classify --> db --> pg
```

`ingest.py` runs the first half (EDGAR to `filings.jsonl`). `classify.py` is shared by the
report and the database loader, so the two can never disagree about which filings are non-standard.

## Roadmap

| Phase | Goal | Status |
|-------|------|--------|
| 1 | Download 10-K, 10-Q and earnings 8-K filings; split into item sections; store them | Complete |
| 2 | LLM extraction of structured signals from the sections, with citations | Not started |
| 3 | Accuracy evaluation against hand labels | Not started |
| 3.5 | Survivorship-free universe: delisted and acquired companies, ticker history | Not started |
| 4 | Event-study backtest of the signals | Not started |
| 5 | Write-up of results | Not started |

## Quick start

Python 3.13 (what it was developed on).

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file in the repo root. The SEC requires a contact string on every request:

```
SEC_USER_AGENT=Your Name your.email@example.com
```

Download and split filings. Documents are cached under `data/cache/`, output is appended to
`data/filings.jsonl`, and a re-run skips filings already done:

```
python ingest.py --tickers AAPL MSFT --max-per-ticker 2
python ingest.py --tickers-file tickers.txt
```

Run the tests (they never touch the network): `pytest`

## Database setup

Needs Postgres 15 or newer (the schema uses `UNIQUE NULLS NOT DISTINCT`). The project was
developed against Supabase. Add the connection string to `.env`:

```
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DBNAME
```

```
python db.py --check              # connect and print the server version; changes nothing
python db.py --create-schema      # run schema.sql (safe to repeat)
python db.py --load               # load data/filings.jsonl (safe to repeat)
python scripts/check_db.py        # compare the database with the JSONL file
```

Row-level security is enabled on all seven tables with no policies, so Supabase's public REST
API (the anon key) cannot read or write them. `db.py` connects directly as the table owner,
which RLS does not restrict. `scripts/check_db.py` fails if any table lacks RLS.

## Scripts

| Script | What it is for |
|--------|----------------|
| `scripts/miss_report.py` | Miss rates per form, non-standard layouts, optional sections, worst filings. Reads `filings.jsonl` only. |
| `scripts/spot_check.py` | Draws a fixed random sample for hand-checking section boundaries, then scores your ticks with a Wilson interval. |
| `scripts/compare_runs.py` | Diffs two ingest outputs filing by filing, to see what a parser change did. |
| `scripts/peek.py` | Saves one company's latest filing as converted text, to see what the splitter receives. Hits the SEC. |
| `scripts/check_db.py` | Live check of the Postgres load against `filings.jsonl`: counts, hashes, RLS, idempotent reload. |

## Project structure

| Path | Purpose |
|------|---------|
| `edgar.py` | SEC client (cache, rate limit, retries) and the `Filing` record |
| `parse.py` | HTML to text, item-section splitting, page-noise cleanup, cross-reference detection |
| `classify.py` | Judges one parsed filing: missing section, non-standard layout, SEC metadata mismatch |
| `ingest.py` | Command-line tool: download, split, append one JSON line per filing |
| `db.py` | Creates the schema and loads `filings.jsonl` into Postgres |
| `schema.sql` | All tables, including the Phase 2-3 ones (`extractions`, `citations`, `labels`) |
| `tickers.txt` | The 25-ticker universe |
| `scripts/` | Reports and live checks (see above) |
| `tests/` | Unit tests, no network |
| `notes/` | Working notes on the SEC data formats |
| `RESULTS.md` | Every measured result, round by round, with root causes |
| `requirements.txt`, `pytest.ini` | Pinned dependencies; test import path |
| `.gitignore`, `.gitattributes`, `LICENSE` | Repo housekeeping (MIT license) |

`data/` (cache and output) is created locally and is gitignored.

## Design decisions

- **Company key is the CIK, not the ticker.** The SEC never changes or reuses a CIK; tickers
  change and get reused. Tickers live in their own table with validity dates.
- **Polite, resilient SEC client.** One place (`EdgarClient.get`) does the disk cache (written
  atomically), the rate limit (8 requests/second, under the SEC's 10) and retries (429 and 5xx
  with 1 to 16 second backoff, honoring `Retry-After`). A 403 stops the run immediately: it means a
  bad User-Agent or a block, and retrying only prolongs a block.
- **Table of contents vs body.** Every item heading appears twice. For each item the parser keeps
  the occurrence with the longest span to the next different item; a TOC entry is followed
  almost at once by the next entry, so its span is tiny.
- **Title fallback for annual-report layouts.** MCD and INTC have no "Item N" headings in the body.
  When Item headings yield fewer than 3 sections of 1,000+ characters, sections are found by
  their standard titles ("RISK FACTORS") instead.
- **8-Ks keep Items 2.02, 7.01, 8.01 and every EX-99.\* exhibit.** The earnings numbers are
  normally in the press release attached as Exhibit 99.x, not in the 8-K itself, and a filing can
  attach several. A few filers (CVX, BAC, GS, PFE) put the results in the 8-K body instead, and
  Item 2.02 often shares one body with Item 7.01. Keeping all of these means no earnings text is lost.
- **`accepted_at` is verified true UTC.** The SEC's `acceptanceDateTime` is UTC; the time shown on
  the filing index page is US Eastern. The raw string is stored too, for auditing. Phase 4 must
  convert to New York time before comparing with market hours.
- **Idempotent loads.** Upserts skip rows that did not change (`IS DISTINCT FROM`), and each section
  carries a SHA-256 of its text. Section ids stay stable across reloads, which later extractions and
  citations rely on. A re-run writes nothing if nothing changed.
- **Network-free tests.** Unit tests use inline HTML and `httpx.MockTransport`. Anything that hits
  the SEC or the database is a script in `scripts/`.
- **Non-standard layouts are reported, not hidden.** Filings whose MD&A is only a pointer are
  flagged (`layout = 'non_standard'` and a pointer kind in the database) and reported separately,
  so the miss rate measures parser failures, not filer layouts.

## Known limitations

- **INTC 10-K section ends.** INTC's annual-report layout has no Item headings, and a section
  there ends only at the next standard title: Items 1A and 7A run too long and Item 7 stops too
  early. Checked by hand on one filing (2026-01-23); the same layout appears in all 6 INTC
  10-Ks. Sample B had no INTC 10-K, so its score does not reflect this. Fix options are in RESULTS.md.
- **48 non-standard filings** (JPM 6 10-Ks and 18 10-Qs, INTC 18 10-Qs, CVX 6 10-Ks) contain only
  a pointer to the MD&A. They are stored as found; their earnings 8-Ks are fully covered.
- **Amended filings (10-K/A, 10-Q/A, 8-K/A) are not ingested.**
- **The 25-ticker universe is today's large caps.** Companies that were delisted or acquired
  never appear, which is survivorship bias and would flatter a backtest. Phase 3.5 fixes this.

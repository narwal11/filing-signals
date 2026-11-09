# filing-signals

An LLM pipeline that reads SEC filings, extracts earnings signals from them, checks those
signals against hand labels, and backtests them. The plan has five phases: download and split
filings into sections (Phase 1), extract signals with an LLM (Phase 2), measure extraction
accuracy against hand labels (Phase 3), backtest the signals (Phase 4), and write up the
results (Phase 5).

**Status:** Phase 1 (ingestion + section extraction) is nearly complete.

## Results so far

Dataset: 25 large-cap tickers, filings since 2021-01-01 (round 3: 143 10-Ks, 430 10-Qs,
587 earnings 8-Ks).

**Usable coverage** (all expected sections found):

| Form  | Usable coverage |
|-------|-----------------|
| 10-K  | 91.6% (131/143) |
| 10-Q  | 91.6% (394/430) |
| 8-K   | 100.0% (587/587) |

The 48 filings not counted come from JPM, INTC and CVX, whose MD&A is only a pointer to
text elsewhere in the filing. Their earnings 8-Ks are fully covered.

**Spot-check accuracy** (section start and end checked by hand):

| Sample | Size | Correct | 95% Wilson interval |
|--------|------|---------|---------------------|
| A (before fixes) | 20 filings, 47 sections | 91.5% (43/47) | 80.1% to 96.6% |
| B (after fixes, fresh sample) | 20 filings, 47 sections | 100.0% (47/47) | 92.4% to 100.0% |

Sample A was used to find bugs, so only sample B is a fair "after" number. Sample B had no
INTC 10-K, so the known INTC limitation (title-fallback section ends run too long) is not
reflected in it. Details and root causes are in [RESULTS.md](RESULTS.md).

## Roadmap

| Phase | Goal | Status |
|-------|------|--------|
| 1 | Ingestion + section extraction (10-K, 10-Q, earnings 8-K) | Nearly complete |
| 2 | LLM signal extraction from sections | Not started |
| 3 | Accuracy evaluation against hand labels | Not started |
| 4 | Backtest the signals | Not started |
| 5 | Write-up of results | Not started |

## Quick start

Python 3.13, Windows PowerShell.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create a `.env` file in the repo root with a contact string for the SEC (required on every request):

```
SEC_USER_AGENT=Your Name your.email@example.com
```

Download and split filings (cached under `data/`, output appended to `data/filings.jsonl`):

```powershell
python ingest.py --tickers AAPL MSFT --max-per-ticker 2
python ingest.py --tickers-file tickers.txt
```

Run the unit tests (no network): `pytest`

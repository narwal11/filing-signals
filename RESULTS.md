# Results

## Phase 1: Section extraction coverage

**Dataset:** 25 large-cap tickers, filings since 2021-01-01:
142 10-Ks, 422 10-Qs, 583 earnings 8-Ks (Item 2.02).

**Expected sections:**
- 10-K: Item 1A (Risk Factors), Item 7 (MD&A), Item 7A (Market Risk)
- 10-Q: Part I Item 2 (MD&A)
- Earnings 8-K: Exhibit 99 press release

**Miss rate** = % of filings missing at least one expected section.
(Rounds 0–1 count a section as present if found at any length.)

| Round | Change                                         | 10-K           | 10-Q        | 8-K          |
|-------|------------------------------------------------|----------------|-------------|--------------|
| 0     | Baseline                                       | 11.3% (16/142) | 0.0% (0/422) | 1.0% (6/583) |
| 1     | Title fallback for annual-report-style 10-Ks (MCD, INTC) | 7.0% (10/142)  | 0.0% (0/422) | 1.0% (6/583) |
| 2     | Tolerant titles, footer cleanup, stricter min-length check; 7A made optional; 8-K body text accepted as coverage | 0.0% (0/142) | 0.0% (0/422) | 0.0% (0/583) |

Note: of the 22 original misses, 10 were fixed by parser changes alone (INTC 6, COST 4).
6 MCD filings had 1A and 7 fixed by the parser, with 7A reclassified as optional (MCD folds
market risk into MD&A). 6 earnings 8-Ks were resolved by redefining coverage: 5 accepted
via substantial 8-K body text (CVX ×2, BAC, GS, PFE) and 1 flagged as an SEC metadata
mismatch (PFE 2023-12-14).

**Non-standard layouts are excluded from the miss rate but reported here:** 12 of 142
10-Ks (8.5%) and 35 of 422 10-Qs (8.3%) use layouts the parser does not split (e.g., JPM's
cross-reference format). **Usable coverage** (all expected sections found):
10-K 91.5% (130/142), 10-Q 91.7% (387/422), 8-K 100.0% (583/583).

Optional sections: 10-K Item 7A found in 95.8% (136/142); 10-Q Part II Item 1A found in
87.2% (368/422).


## Phase 1: Spot-check accuracy (hand-checked)

**Accuracy** = % of sampled sections whose start and end are correct, checked by hand
(`scripts/spot_check.py`, seed 42). The sample is 20 filings, one per company, drawn only from
filings where every expected section was found.

| Round data | Sample | 10-K | 10-Q | 8-K | All sections |
|------------|--------|------|------|-----|--------------|
| 2 | 20 filings, 47 sections | 85.7% (18/21) | 100.0% (14/14) | 91.7% (11/12) | 91.5% (43/47), 95% Wilson interval 80.1% to 96.6% |

Note: this is not comparable with the miss-rate table above. It measures whether the section
boundaries are right, not whether a section was found, and it excludes the 47 non-standard-layout
filings. Sections from one filing are not independent, so the interval is somewhat optimistic.

## Root causes found
- **Annual-report layout (MCD, INTC):** body sections are headed by plain titles
  ("RISK FACTORS"), and "Item 1A" appears only in a cross-reference index. Fixed in round 1.
- **Title wording variants (COST):** "Financial Conditions" (plural) broke exact title matching.
- **Earnings 8-Ks without Exhibit 99 (CVX, PFE, BAC, GS):** results are in the 8-K body.
- **SEC metadata mismatch (PFE 2023-12-14):** tagged Item 2.02, but the document is Item 2.01.
- **Non-standard layout (JPM):** Item 7 is a cross-reference to the MD&A elsewhere.
- **Title-fallback sections end too late (INTC 1A, 7, 7A):** they stop only at the next standard
  title, so they run through non-standard headings ("Rule 10b5-1 Trading Arrangements",
  "Critical Accounting Estimates"). Found by spot check; not fixed (needs a page-based split).
- **Last 8-K item runs to the end of the document (XOM 2.02):** no "Item 9.01" heading, so 2.02
  includes the exhibit index and signatures; the combined "Item 2.02 / Item 7.01" heading also
  gives both items the same text. Found by spot check; not fixed.
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
| 3     | Boundary fixes from spot check A: 8-K items end at SIGNATURES / exhibit index, combined 8-K headings, title fallback skips the table of contents; page-noise and SEC exhibit-header removal; heading line kept in section text | 0.0% (0/143) | 0.0% (0/430) | 0.0% (0/587) |

Note on round 3 totals: the counts rose from 142/422/583 to 143/430/587 (and non-standard from
47 to 48) because 13 filings came out after round 2 (AAPL 10-K and 8-K; 10-Qs from AMD, MCD, PFE,
CVX, UNH, GS, JPM, XOM; 8-Ks from AMD, MCD, PFE), not because of any parser change. The "Dataset"
line above describes rounds 0–2. On the 1,147 filings in both rounds no outcome changed; all 13 new filings
are ok except one new JPM 10-Q (non-standard). From round 3 on, section text starts with its
heading line and has page footers removed, so character counts are not comparable with earlier
rounds. Round 3 changed boundaries in 6 INTC 10-K Item 7s (start moved past 60–92K chars of
table of contents and business text) and 33 8-K items in 31 filings (KO 24, XOM 4, CVX 2, BAC,
GS, PFE: exhibit index and signatures removed). Round 3 usable coverage: 10-K 91.6% (131/143),
10-Q 91.6% (394/430), 8-K 100.0% (587/587); non-standard 48 filings (JPM 6 10-Ks and 18 10-Qs,
INTC 18 10-Qs, CVX 6 10-Ks).

Note: of the 22 original misses, 10 were fixed by parser changes alone (INTC 6, COST 4).
6 MCD filings had 1A and 7 fixed by the parser, with 7A reclassified as optional (MCD folds
market risk into MD&A). 6 earnings 8-Ks were resolved by redefining coverage: 5 accepted
via substantial 8-K body text (CVX ×2, BAC, GS, PFE) and 1 flagged as an SEC metadata
mismatch (PFE 2023-12-14).

**Non-standard layouts are excluded from the miss rate but reported here:** 12 of 142
10-Ks (8.5%) and 35 of 422 10-Qs (8.3%) use layouts the parser does not split (e.g., JPM's
cross-reference format). **Usable coverage** (all expected sections found):
10-K 91.5% (130/142), 10-Q 91.7% (387/422), 8-K 100.0% (583/583).

All 47 non-standard filings come from 3 companies whose MD&A section is only a pointer
to text elsewhere in the filing: JPM (6 10-Ks, 17 10-Qs), INTC (18 10-Qs), CVX (6 10-Ks).
Their earnings 8-K press releases are fully covered, so all 25 companies still have
earnings text for Phase 2.

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
  "Critical Accounting Estimates"). Found by spot check. **Known limitation, not fixed in round 3**
  (see below).
- **Title-fallback section starts in the table of contents (INTC 7):** the main TOC lists
  "Management's Discussion and Analysis" on its own line, and that line was taken as the heading.
  Fixed in round 3: a title line followed by TOC entries is skipped.
- **Last 8-K item runs to the end of the document (XOM 2.02, KO, CVX, BAC, GS, PFE):** with no
  "Item 9.01" heading, the last item included the exhibit index and signatures. The combined
  "Item 2.02 / Item 7.01" heading also needed both keys. Fixed in round 3: 8-K items end at
  SIGNATURES / INDEX TO EXHIBITS / EXHIBIT INDEX, and one heading line can open several items.
- **Page noise inside sections (UNH, DIS, MSFT, NKE, COST, …):** page footers like
  "35 Table of Contents", running headers like "48 PART II", and trailing page numbers. Fixed in
  round 3.
- **SEC packaging line on exhibits:** every exhibit started with "EX-99.1 2 file.htm EX-99.1".
  Fixed in round 3 (one MSFT EX-99.3 held nothing else and is now dropped as empty).

### Known limitation: INTC 10-K section ends
INTC's 10-K is in annual-report order, and the parser only ends a title-fallback section at the
next *standard* title. Boundaries from the hand check of the 2026-01-23 filing (sample A):

| Section | Parser ends at | Real end |
|---------|----------------|----------|
| 1A | After "Other Key Information" (incl. "Rule 10b5-1 Trading Arrangements"), at "Cybersecurity" | "...requiring us to alter manufacturing and assembly and test processes." (end of the environmental regulations risk, p.51) |
| 7 | Stops at "Properties" (p.32), which Intel places *inside* MD&A | Runs on through Market Risk (p.33) and Critical Accounting Estimates (p.34), which are also MD&A |
| 7A | After Critical Accounting Estimates (Inventories ... Loss Contingencies), at "Risk Factors" on p.37 | "...mitigate the risk of a potential supplier concentration for our key commodities." (end of Commodity Price Risk, p.34) |

The Item 7 *start* (main table of contents instead of the real MD&A on p.18) was fixed in
round 3. The other ends are the same layout in all 6 INTC 10-Ks, but only the 2026-01-23 filing
was checked by hand. Not fixed in round 3: no general fix was clean enough. Options:
- **A. Running-footer regions:** INTC pages carry a footer label ("Risk Factors |",
  "MD&A |", "Other Key Information |"); a section may not cross a label change. Cheapest; fixes
  the 1A and 7 ends, not 7A; useless for filers with one label or none (MCD).
- **B. TOC hierarchy:** read the filer's TOC to tell group titles from sub-entries and end each
  section at its next sibling. Would fix 7A too, but the flattened text loses most of the
  hierarchy, so it would be fragile and tuned on one filer.
- **C. Page-based split:** map the cross-reference index pages to page footers and cut on page
  numbers. Most precise and also the base for resolving page pointers (Future work), but the
  biggest build.

## Future work
- **Resolve page pointers.** The non-standard-layout filings (47 in round 2, 48 in round 3: JPM
  6 10-Ks and 18 10-Qs, INTC 18 10-Qs, CVX 6 10-Ks) only contain a pointer to where the MD&A is ("appears on pages
  46-160", "presented in the Financial Table of Contents"). They stay classified as
  non-standard for now and are not recovered in this round. A fix would follow the pointer
  inside the same document, probably by splitting on page numbers from the filer's own index and
  the page footers. The same page-based split would also tighten the section ends of the
  title-fallback filings (INTC 1A, 7, 7A).
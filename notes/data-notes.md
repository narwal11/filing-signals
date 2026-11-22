# Data notes

## 10-K
- Apple's latest 10-K filed on: 2025-10-31
- Accession number: 0000320193-25-000079
- Where Item 1A starts (first words): The following summarizes factors that could have a material adverse effect on the Company’s business, reputation, results of operations, financial condition and stock price. The Company may not be able to accurately predict, control or mitigate these risks. 
- How the table-of-contents copy differs from the real one: In the table of contents, right after Item 1A, there actually is no real information, but the next words are the next headers. During the next Item 1A search inside the information, the information following is the actual data for the header. There can be some way that a program measures how long the data towards the next item is.

## 8-K (earnings)
- Filing date: 2026-07-30
- What the 8-K body says: Nothing much, just points to the press release in Exhibit 99.1
- Where the actual numbers are: Exhibit 99.1
- Exhibit 99.1 filename: a8-kex991q3202606272026.htm

## 10-Q
- What's different about the item numbering: Item 1 appears multiple times, in Part I and Part II. The program should be able to read or know which part the specific item is in.

## JSON
- Apple's CIK: 320193, ticker: "AAPL", title: "Apple Inc." 
- How the submissions JSON is structured: 10 digit number padded with 0s. The lists in filings.recent are parallel: position N in form, accessionNumber, primaryDocument, and filingDate all describe the same filing
- URL I built by hand: https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm

## SEC rules
- User-Agent requirement: Sample Company Name AdminContact@<sample company domain>.com | Accept-Encoding: gzip, deflate | Host: sec.gov | User-Agent must be my name and email
- Rate limit: Current max request rate: 10 requests/second | The SEC does not allow botnets or automated tools to crawl the site. Any request that has been identified as part of a botnet or an automated tool outside of the acceptable policy will be managed to ensure fair access for all users.

The rules for accessing Edgar are probably in place in order to ensure everyone has equitable access to SEC EDGAR content The email lets the SEC contact me if my script misbehaves, instead of just blocking my IP.

## Acceptance timestamp timezone (checked 2026-11-22)
- EDGAR submissions JSON `acceptanceDateTime` is true UTC (the "Z" is correct).
- The "Accepted" time on the filing index page is US Eastern time.
- Evidence:
  - 0001140361-26-042798 (Nov 2026): JSON 21:30:40Z = index 16:30:40 EST (UTC-5)
  - 0001140361-26-015711 (Apr 2026): JSON 21:29:51Z = index 17:29:51 EDT (UTC-4)
- For Phase 4: convert to America/New_York before comparing to market hours
  (9:30-16:00 ET). Accepted after 16:00 ET -> event day = next trading day.
## Citation offsets (rule for Phase 2, set 2026-11-22)
- A citation's `start_offset`/`end_offset` are always relative to the text of ONE
  section (`citations.section_id`), never to a filing or a concatenated prompt.
- Offsets are 0-based and half-open, like Python slicing: `text[start:end]`.
  Postgres `substr` is 1-based: `substr(text, start + 1, end - start)`.
- If an extraction input concatenates sections (e.g. 8-K Item 2.02 + EX-99.1), the
  extraction code maps each offset back to its section, and subtracts that section's
  start in the concatenated input, before saving.
- `extractions.input_text_sha256` records the exact text the model saw. A citation
  is only checkable against the section text whose `text_sha256` it was made from.

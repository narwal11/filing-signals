-- Postgres schema for filing-signals. Run by db.create_schema().
-- Safe to run again: every statement is IF NOT EXISTS.
-- Needs Postgres 15+ (UNIQUE NULLS NOT DISTINCT on tickers).

-- ---- Phase 1: companies, tickers, filings, sections ------------------------

-- Keyed by CIK, not ticker: the SEC never changes or reuses a CIK, but tickers change
-- and get reused (Phase 3.5 adds delisted and acquired firms).
CREATE TABLE IF NOT EXISTS companies (
    cik  bigint PRIMARY KEY,  -- bigint: the CIK format allows 10 digits, more than integer holds
    name text   NOT NULL      -- current conformed name from the SEC
);

-- Ticker history. Kept out of `filings` on purpose: the ticker in filings.jsonl is the one
-- used at ingest time, not the one the company had on the filing date.
CREATE TABLE IF NOT EXISTS tickers (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cik        bigint NOT NULL REFERENCES companies (cik) ON DELETE CASCADE,
    ticker     text   NOT NULL,
    valid_from date,  -- NULL = unknown (all rows today; Phase 3.5 fills these in)
    valid_to   date,  -- NULL = unknown or still current
    UNIQUE NULLS NOT DISTINCT (cik, ticker, valid_from)
);
-- Ticker -> CIK lookup: WHERE ticker = 'AAPL' AND valid_to IS NULL
CREATE INDEX IF NOT EXISTS tickers_ticker_idx ON tickers (ticker);

CREATE TABLE IF NOT EXISTS filings (
    -- Natural key: assigned by the SEC, never changes, appears in every URL.
    accession             text PRIMARY KEY CHECK (accession ~ '^\d{10}-\d{2}-\d{6}$'),
    -- Never derive this from the accession prefix: that prefix is the filer agent's CIK.
    cik                   bigint NOT NULL REFERENCES companies (cik) ON DELETE RESTRICT,
    form                  text NOT NULL CHECK (form IN ('10-K', '10-Q', '8-K')),
    filed_date            date NOT NULL,
    accepted_at           timestamptz NOT NULL,  -- EDGAR acceptanceDateTime; verified to be true UTC
    accepted_at_raw       text,                  -- the exact SEC string, for auditing
    report_date           date,
    sec_items             text[] NOT NULL DEFAULT '{}',  -- 8-K items per SEC metadata
    items_found           text[] NOT NULL DEFAULT '{}',  -- item headings the parser saw in the document
    primary_document      text NOT NULL,                 -- URL is rebuilt from cik + accession + this
    layout                text NOT NULL DEFAULT 'standard' CHECK (layout IN ('standard', 'non_standard')),
    sec_metadata_mismatch boolean NOT NULL DEFAULT false,  -- from classify.check_filing
    loaded_at             timestamptz NOT NULL DEFAULT now()
);
-- Point-in-time lookup: WHERE cik = $1 AND accepted_at <= $2 ORDER BY accepted_at DESC LIMIT 1
-- and a company's filing timeline.
CREATE INDEX IF NOT EXISTS filings_cik_accepted_idx ON filings (cik, accepted_at);

CREATE TABLE IF NOT EXISTS sections (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    accession    text NOT NULL REFERENCES filings (accession) ON DELETE CASCADE,
    key          text NOT NULL,  -- '1A', '7', 'I-2', '2.02', 'EX-99.1', ...
    title        text NOT NULL,
    heading      text NOT NULL,  -- may be ''
    source       text NOT NULL,  -- 'primary' or the exhibit type, e.g. 'EX-99.1'
    text         text NOT NULL,
    -- Generated, so it can never drift from the text. length() counts characters, like Python len().
    char_count   integer GENERATED ALWAYS AS (length(text)) STORED,
    -- SHA-256 of the UTF-8 text, computed by db.py. (It can't be a generated column: convert_to()
    -- is not IMMUTABLE.) scripts/check_db.py re-hashes every section in SQL to confirm it matches.
    text_sha256  bytea   NOT NULL,
    pointer_kind text CHECK (pointer_kind IN ('toc', 'page', 'name')),  -- only for cross-reference sections
    -- Natural identity; its index also serves "all sections of filing X".
    UNIQUE (accession, key),
    -- Target for the composite FK on extractions (section must belong to the same filing).
    UNIQUE (id, accession)
);

-- ---- Phase 2: extractions and citations (created now, filled later) ---------

CREATE TABLE IF NOT EXISTS extractions (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,  -- one row per model call
    accession         text   NOT NULL REFERENCES filings (accession) ON DELETE RESTRICT,
    section_id        bigint,  -- NULL = the extraction used the whole filing
    model             text   NOT NULL,
    prompt_version    text   NOT NULL,
    output            jsonb,   -- parsed result
    error             text,    -- set instead of output when the call or parsing failed
    input_text_sha256 bytea  NOT NULL,  -- hash of the exact text the model saw
    input_tokens      integer NOT NULL,
    output_tokens     integer NOT NULL,
    cost_usd          numeric(12, 6) NOT NULL,  -- numeric, not float: sums are exact
    latency_ms        integer NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CHECK ((output IS NULL) <> (error IS NULL)),  -- exactly one of the two
    -- If a section is named, it must belong to this filing.
    FOREIGN KEY (section_id, accession) REFERENCES sections (id, accession) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS extractions_lookup_idx ON extractions (accession, model, prompt_version);
CREATE INDEX IF NOT EXISTS extractions_section_idx ON extractions (section_id);  -- FKs aren't auto-indexed

CREATE TABLE IF NOT EXISTS citations (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    extraction_id bigint NOT NULL REFERENCES extractions (id) ON DELETE CASCADE,
    section_id    bigint NOT NULL REFERENCES sections (id) ON DELETE RESTRICT,
    field         text NOT NULL,     -- the output field this citation supports
    start_offset  integer NOT NULL,  -- 0-based, half-open: Python text[start_offset:end_offset]
    end_offset    integer NOT NULL,
    quote         text NOT NULL,     -- what the model said it quoted
    verified      boolean NOT NULL,  -- text[start_offset:end_offset] == quote
    CHECK (0 <= start_offset AND start_offset < end_offset)
);
CREATE INDEX IF NOT EXISTS citations_extraction_idx ON citations (extraction_id);
CREATE INDEX IF NOT EXISTS citations_section_idx ON citations (section_id);

COMMENT ON TABLE citations IS
    'Offsets are always relative to the text of ONE section (section_id). If an extraction input '
    'concatenates sections, the extraction code maps each offset back to its section before saving. '
    'Postgres substr is 1-based: substr(text, start_offset + 1, end_offset - start_offset).';

-- ---- Phase 3: hand labels --------------------------------------------------

CREATE TABLE IF NOT EXISTS labels (
    accession  text NOT NULL REFERENCES filings (accession) ON DELETE RESTRICT,
    field      text NOT NULL,
    value      jsonb NOT NULL,  -- JSON null = "the filing does not state this"; no row = not labeled yet
    notes      text,
    labeled_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (accession, field)  -- one labeler, one label per field
);

-- ---- Supabase API lockdown -------------------------------------------------
-- Supabase exposes tables in `public` through its REST API to anyone holding the
-- project's anon key. Row-level security with no policies denies that API all access.
-- db.py connects directly as the table owner, which RLS does not restrict.
-- (Harmless on plain Postgres.)
ALTER TABLE companies   ENABLE ROW LEVEL SECURITY;
ALTER TABLE tickers     ENABLE ROW LEVEL SECURITY;
ALTER TABLE filings     ENABLE ROW LEVEL SECURITY;
ALTER TABLE sections    ENABLE ROW LEVEL SECURITY;
ALTER TABLE extractions ENABLE ROW LEVEL SECURITY;
ALTER TABLE citations   ENABLE ROW LEVEL SECURITY;
ALTER TABLE labels      ENABLE ROW LEVEL SECURITY;

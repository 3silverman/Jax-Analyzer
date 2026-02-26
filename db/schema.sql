-- ============================================================
-- Jacksonville Investment Property Analyzer — Postgres Schema
-- Run once against your Supabase project.
-- ============================================================

-- Enable UUID extension (already on Supabase by default)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── properties ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS properties (
    canonical_id        TEXT        PRIMARY KEY,   -- 12-char SHA1 hash of address+zip
    source              TEXT        NOT NULL,       -- 'zillow_sale' | 'rentcast' | ...
    source_id           TEXT,
    scraped_at          TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Location
    address             TEXT        NOT NULL,
    city                TEXT        NOT NULL DEFAULT 'Jacksonville',
    state               TEXT        NOT NULL DEFAULT 'FL',
    zip_code            TEXT        NOT NULL,
    lat                 DOUBLE PRECISION,
    lon                 DOUBLE PRECISION,

    -- Property
    price               DOUBLE PRECISION NOT NULL,
    beds                INT,
    baths               DOUBLE PRECISION,
    sqft                DOUBLE PRECISION,
    year_built          INT,
    property_type       TEXT        NOT NULL DEFAULT 'unknown',
    num_units           INT         NOT NULL DEFAULT 1,
    lot_size_sqft       DOUBLE PRECISION,
    days_on_market      INT,

    -- Data quality
    confidence_score    DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    missing_fields      TEXT[]      NOT NULL DEFAULT '{}',

    -- Status
    status              TEXT        NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'failed_gates', 'shortlisted', 'closed')),

    -- Raw payload
    raw                 JSONB       NOT NULL DEFAULT '{}',

    CONSTRAINT properties_zip_check CHECK (length(zip_code) = 5)
);

CREATE INDEX IF NOT EXISTS idx_properties_zip_code   ON properties (zip_code);
CREATE INDEX IF NOT EXISTS idx_properties_status      ON properties (status);
CREATE INDEX IF NOT EXISTS idx_properties_scraped_at  ON properties (scraped_at DESC);
CREATE INDEX IF NOT EXISTS idx_properties_price       ON properties (price);


-- ── rental_comps ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS rental_comps (
    id                  UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    canonical_id        TEXT        NOT NULL,   -- comp's own hash
    property_id         TEXT        REFERENCES properties (canonical_id) ON DELETE SET NULL,
    source              TEXT        NOT NULL,
    source_id           TEXT,
    scraped_at          TIMESTAMPTZ NOT NULL,

    address             TEXT        NOT NULL,
    zip_code            TEXT        NOT NULL,
    lat                 DOUBLE PRECISION,
    lon                 DOUBLE PRECISION,

    beds                INT,
    baths               DOUBLE PRECISION,
    sqft                DOUBLE PRECISION,

    rental_strategy     TEXT        NOT NULL CHECK (rental_strategy IN ('ltr','mtr','str')),
    monthly_rate        DOUBLE PRECISION,
    adr                 DOUBLE PRECISION,
    utilities_included  BOOLEAN     NOT NULL DEFAULT FALSE,

    raw                 JSONB       NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_comps_property_id     ON rental_comps (property_id);
CREATE INDEX IF NOT EXISTS idx_comps_zip_strategy    ON rental_comps (zip_code, rental_strategy);
CREATE INDEX IF NOT EXISTS idx_comps_scraped_at      ON rental_comps (scraped_at DESC);


-- ── neighborhood_scores ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS neighborhood_scores (
    id                  UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    property_id         TEXT        NOT NULL REFERENCES properties (canonical_id) ON DELETE CASCADE,
    scored_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Liveability index
    liveability_total   INT         NOT NULL DEFAULT 0,
    walk_pts            INT         NOT NULL DEFAULT 0,
    hospital_pts        INT         NOT NULL DEFAULT 0,
    crime_pts           INT         NOT NULL DEFAULT 0,
    flood_pts           INT         NOT NULL DEFAULT 0,
    visual_pts          INT         NOT NULL DEFAULT 5,

    -- Raw scores
    walk_score          INT,
    transit_score       INT,
    bike_score          INT,
    crime_grade         TEXT,
    crime_low_confidence BOOLEAN    NOT NULL DEFAULT FALSE,
    flood_zone          TEXT,
    flood_high_risk     BOOLEAN     NOT NULL DEFAULT FALSE,
    hospital_distance_miles DOUBLE PRECISION,
    closest_hospital    TEXT,
    proximity_score     INT         NOT NULL DEFAULT 0,

    -- Gate result
    passed_gates        BOOLEAN     NOT NULL DEFAULT FALSE,
    failed_gate_reasons TEXT[]      NOT NULL DEFAULT '{}'
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_neighborhood_property ON neighborhood_scores (property_id);


-- ── deal_scores ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS deal_scores (
    id                  UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    property_id         TEXT        NOT NULL REFERENCES properties (canonical_id) ON DELETE CASCADE,
    scored_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Component scores
    return_score        INT         NOT NULL DEFAULT 0,
    risk_score          INT         NOT NULL DEFAULT 0,
    confidence_score    INT         NOT NULL DEFAULT 0,
    deal_score          INT         NOT NULL DEFAULT 0,

    -- Alert
    is_high_priority    BOOLEAN     NOT NULL DEFAULT FALSE,
    alert_sent          BOOLEAN     NOT NULL DEFAULT FALSE,
    alert_reasons       TEXT[]      NOT NULL DEFAULT '{}',

    -- Key metrics
    conservative_cash_flow DOUBLE PRECISION,
    best_strategy       TEXT,
    dscr                DOUBLE PRECISION,
    cash_on_cash        DOUBLE PRECISION,

    -- Explanation
    why_scored_high     TEXT,

    -- Snapshot of assumptions used for this score (for audit)
    assumptions_snapshot JSONB      NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_deal_scores_property   ON deal_scores (property_id);
CREATE INDEX IF NOT EXISTS idx_deal_scores_score_desc ON deal_scores (deal_score DESC);
CREATE INDEX IF NOT EXISTS idx_deal_scores_is_hp      ON deal_scores (is_high_priority) WHERE is_high_priority = TRUE;


-- ── outcomes ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS outcomes (
    id                  UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    property_id         TEXT        NOT NULL REFERENCES properties (canonical_id) ON DELETE CASCADE,
    outcome             TEXT        NOT NULL CHECK (outcome IN ('pursued','rejected','offer_made','closed')),
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_outcomes_property ON outcomes (property_id);


-- ── scan_log ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scan_log (
    scan_id             UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ,
    properties_scanned  INT         NOT NULL DEFAULT 0,
    passed_gates        INT         NOT NULL DEFAULT 0,
    alerts_triggered    INT         NOT NULL DEFAULT 0,
    errors              JSONB       NOT NULL DEFAULT '[]'
);


-- ── assumptions ──────────────────────────────────────────────
-- Single-row config table.  Enforce with a check constraint.
CREATE TABLE IF NOT EXISTS assumptions (
    id                  INT         PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    interest_rate       DOUBLE PRECISION NOT NULL DEFAULT 0.075,
    insurance_pct       DOUBLE PRECISION NOT NULL DEFAULT 0.005,
    ltr_vacancy         DOUBLE PRECISION NOT NULL DEFAULT 0.08,
    mtr_vacancy         DOUBLE PRECISION NOT NULL DEFAULT 0.10,
    str_vacancy         DOUBLE PRECISION NOT NULL DEFAULT 0.25,
    mgmt_rate           DOUBLE PRECISION NOT NULL DEFAULT 0.08,
    capex_rate          DOUBLE PRECISION NOT NULL DEFAULT 0.005,
    maintenance_rate    DOUBLE PRECISION NOT NULL DEFAULT 0.01,

    -- VA loan defaults
    va_funding_fee_pct  DOUBLE PRECISION NOT NULL DEFAULT 0.0215,
    loan_term_years     INT              NOT NULL DEFAULT 30,
    closing_costs_pct   DOUBLE PRECISION NOT NULL DEFAULT 0.03,

    -- Scoring thresholds
    alert_threshold     INT              NOT NULL DEFAULT 85,
    min_cash_flow       DOUBLE PRECISION NOT NULL DEFAULT 500.0,
    min_dscr            DOUBLE PRECISION NOT NULL DEFAULT 1.2,

    -- Live VA rate (fetched from FRED / cached daily)
    current_va_rate     DOUBLE PRECISION,          -- decimal (e.g. 0.0691); NULL = never fetched
    rate_fetched_at     TIMESTAMPTZ,               -- UTC timestamp of last successful fetch
    rate_is_stale       BOOLEAN          NOT NULL DEFAULT FALSE,
    rate_source         TEXT             NOT NULL DEFAULT 'fallback'
                        CHECK (rate_source IN ('fred', 'cache', 'fallback'))
);

-- Seed default row
INSERT INTO assumptions (id) VALUES (1) ON CONFLICT DO NOTHING;

-- Schema and tables for all layers.
-- Executed inside one transaction by the runner; safe to re-run (IF NOT EXISTS).

CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS marts;
CREATE SCHEMA IF NOT EXISTS pipeline;

-- ---------------------------------------------------------------------------
-- RAW: every CSV row exactly as delivered (all columns TEXT), plus provenance.
-- The CSV is a full snapshot; a changed file hash replaces all rows (D5).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.ember_india_monthly (
    raw_id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    row_number         integer  NOT NULL,
    country            text,
    country_code       text,
    state              text,
    state_code         text,
    state_type         text,
    date               text,
    category           text,
    subcategory        text,
    variable           text,
    unit               text,
    value              text,
    yoy_absolute_change text,
    yoy_pct_change     text,
    source_url         text     NOT NULL,
    file_sha256        text     NOT NULL,
    loaded_at          timestamptz NOT NULL DEFAULT now()
);

-- One row per download/load attempt (skipped loads are recorded too).
CREATE TABLE IF NOT EXISTS raw.file_loads (
    load_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_url     text        NOT NULL,
    file_sha256    text        NOT NULL,
    file_bytes     bigint      NOT NULL,
    row_count      bigint      NOT NULL,
    downloaded_at  timestamptz NOT NULL,
    loaded_at      timestamptz NOT NULL DEFAULT now(),
    action         text        NOT NULL CHECK (action IN ('loaded', 'replaced', 'skipped_unchanged'))
);

-- ---------------------------------------------------------------------------
-- PIPELINE: rejects and run log.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pipeline.rejects (
    reject_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_sha256  text        NOT NULL,
    row_number   integer,
    state        text,
    date         text,
    category     text,
    subcategory  text,
    variable     text,
    unit         text,
    value        text,
    reason       text        NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_rejects_reason ON pipeline.rejects (reason);

CREATE TABLE IF NOT EXISTS pipeline.run_log (
    run_id            uuid        PRIMARY KEY,
    command           text        NOT NULL,
    target            text        NOT NULL,
    started_at        timestamptz NOT NULL,
    finished_at       timestamptz,
    status            text        NOT NULL CHECK (status IN ('success', 'failed')),
    file_sha256       text,
    raw_rows_in       bigint,
    staging_rows_out  bigint,
    fact_rows_out     bigint,
    rejects           bigint,
    message           text,
    details           jsonb
);

-- ---------------------------------------------------------------------------
-- STAGING: typed rows + the state mapping (D6).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS staging.state_mapping (
    mapping_id       integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reporting_state  text   NOT NULL,           -- exactly as in the CSV 'State' column
    valid_from       date   NOT NULL DEFAULT '1900-01-01',
    valid_to         date,                      -- NULL = open-ended
    canonical_state  text   NOT NULL,           -- entity key used in marts
    display_name     text   NOT NULL,
    note             text,
    CONSTRAINT uq_state_mapping UNIQUE (reporting_state, valid_from)
);

CREATE TABLE IF NOT EXISTS staging.power_monthly (
    staging_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reporting_state  text   NOT NULL,
    state_code       text,
    state_type       text,
    month_date       date   NOT NULL CHECK (month_date = date_trunc('month', month_date)::date),
    category         text   NOT NULL,
    subcategory      text   NOT NULL,
    variable         text   NOT NULL,
    unit             text   NOT NULL,
    metric           text   NOT NULL,
    value            double precision,
    file_sha256      text   NOT NULL,
    CONSTRAINT uq_staging_row
        UNIQUE (reporting_state, month_date, category, subcategory, variable, unit, metric)
);

-- ---------------------------------------------------------------------------
-- MARTS: dimensions.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS marts.dim_state (
    state_key       integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    canonical_state text   NOT NULL UNIQUE,
    display_name    text   NOT NULL,
    state_code      text,
    state_type      text,
    first_month     date,
    last_month      date
);

-- Which label was reported, and when (documents the J&K/Ladakh change, D6).
CREATE TABLE IF NOT EXISTS marts.dim_state_reporting (
    state_key       integer NOT NULL REFERENCES marts.dim_state (state_key),
    reporting_state text    NOT NULL,
    valid_from      date    NOT NULL,
    valid_to        date,
    note            text,
    PRIMARY KEY (state_key, reporting_state, valid_from)
);

CREATE TABLE IF NOT EXISTS marts.dim_source (
    source_key    integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_name   text    NOT NULL,   -- CSV 'Variable'
    subcategory   text    NOT NULL,   -- 'Fuel' | 'Aggregate fuel' | 'Total' | 'CO2 intensity'
    is_aggregate  boolean NOT NULL,
    is_renewable  boolean NOT NULL,
    is_fossil     boolean NOT NULL,
    is_total      boolean NOT NULL,
    display_order integer NOT NULL DEFAULT 100,
    CONSTRAINT uq_dim_source UNIQUE (source_name, subcategory)
);

CREATE TABLE IF NOT EXISTS marts.dim_date (
    date_key   integer PRIMARY KEY,   -- YYYYMM
    month_date date    NOT NULL UNIQUE,
    year       integer NOT NULL,
    month      integer NOT NULL,
    quarter    integer NOT NULL,
    year_month text    NOT NULL
);

CREATE TABLE IF NOT EXISTS marts.fact_power_monthly (
    state_key  integer NOT NULL REFERENCES marts.dim_state (state_key),
    date_key   integer NOT NULL REFERENCES marts.dim_date (date_key),
    source_key integer NOT NULL REFERENCES marts.dim_source (source_key),
    metric     text    NOT NULL,
    value      double precision,
    PRIMARY KEY (state_key, date_key, source_key, metric)
);
CREATE INDEX IF NOT EXISTS ix_fact_metric ON marts.fact_power_monthly (metric, date_key);

-- ---------------------------------------------------------------------------
-- MARTS: derived tables (computed in SQL from fuel-level rows, D8).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS marts.renewables_share_monthly (
    state_key           integer NOT NULL REFERENCES marts.dim_state (state_key),
    date_key            integer NOT NULL REFERENCES marts.dim_date (date_key),
    renewable_gwh       double precision,
    total_gwh           double precision,
    renewables_share    double precision,       -- 0..1, derived
    publisher_share     double precision,       -- the dataset's own % row, cross-check
    PRIMARY KEY (state_key, date_key)
);

CREATE TABLE IF NOT EXISTS marts.yoy_change_monthly (
    state_key       integer NOT NULL REFERENCES marts.dim_state (state_key),
    date_key        integer NOT NULL REFERENCES marts.dim_date (date_key),
    source_key      integer NOT NULL REFERENCES marts.dim_source (source_key),
    metric          text    NOT NULL,
    value           double precision,
    value_year_ago  double precision,
    yoy_abs         double precision,
    yoy_pct         double precision,
    PRIMARY KEY (state_key, date_key, source_key, metric)
);

-- Rankings per month. rank_total_generation: 1 = largest.
-- rank_renewables_share: 1 = highest share. rank_carbon_intensity: 1 = cleanest.
CREATE TABLE IF NOT EXISTS marts.state_rankings_monthly (
    date_key                  integer NOT NULL REFERENCES marts.dim_date (date_key),
    state_key                 integer NOT NULL REFERENCES marts.dim_state (state_key),
    total_generation_gwh      double precision,
    renewables_share          double precision,
    carbon_intensity_gco2kwh  double precision,
    rank_total_generation     integer,
    rank_renewables_share     integer,
    rank_carbon_intensity     integer,
    PRIMARY KEY (date_key, state_key)
);

-- Carbon intensity trend per entity (each state/UT and the national rows),
-- with the year-over-year change joined from yoy_change_monthly.
CREATE TABLE IF NOT EXISTS marts.carbon_intensity_trend (
    state_key         integer NOT NULL REFERENCES marts.dim_state (state_key),
    date_key          integer NOT NULL REFERENCES marts.dim_date (date_key),
    carbon_intensity  double precision,
    yoy_abs           double precision,
    yoy_pct           double precision,
    PRIMARY KEY (state_key, date_key)
);

-- National series = the file's own "India Total" rows (which equal the sum of
-- states to within 5e-7 relative; verified against the source file).
CREATE TABLE IF NOT EXISTS marts.national_monthly (
    date_key                  integer PRIMARY KEY REFERENCES marts.dim_date (date_key),
    total_generation_gwh      double precision,
    emissions_ktco2           double precision,
    renewables_share          double precision,   -- 0..100, publisher aggregate row
    carbon_intensity_gco2kwh  double precision    -- publisher CO2 intensity row
);

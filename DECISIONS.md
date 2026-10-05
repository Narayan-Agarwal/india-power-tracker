# DECISIONS.md

Every significant decision made while building this project, with what was
decided, why, and the alternatives considered.

---

## D1. Data source: Ember India electricity data (monthly, long-format CSV)

**What:** Use Ember's "India monthly electricity data – long format (CSV)" as
the single data source.

**Verified on 2026-10-05:**
- Dataset page: `https://ember-energy.org/data/india-electricity-data`
  (site formerly at `ember-climate.org`, now redirects).
- Download URL used by the pipeline:
  `https://files.ember-energy.org/public-downloads/india_monthly_full_release_long_format.csv`
  (link labelled "India monthly electricity data – long format (CSV)" on the
  page above; confirmed by fetching the page and by downloading the file).
- Licence, quoted from the page footer of ember-energy.org: **"All content is
  released under a Creative Commons Attribution Licence (CC-BY-4.0)."**
- Methodology PDF ("Download methodology (PDF)" link on the page):
  `https://files.ember-energy.org/public-downloads/ember_subnational_data_methodology.pdf`

**Why:** the task specified Ember; the dataset is state-level, monthly, open,
and covers generation, capacity, emissions and carbon intensity.

**Alternatives:** CEA (Central Electricity Authority) reports are the
upstream source but are PDF/Excel digests without a stable machine-readable
archive; NPP APIs are undocumented. Ember aggregates CEA/MNRE and publishes a
stable CSV under CC BY 4.0.

## D2. Ember site blocks plain HTTP clients; URL discovery path

**What:** `ember-energy.org` sits behind a Cloudflare challenge that returns
HTTP 403 to plain `curl`/`urllib` requests (verified 2026-10-05). The data
files themselves live on `files.ember-energy.org`, which does **not** block
programmatic download. The pipeline downloads the CSV directly from
`files.ember-energy.org`, so no scraping of the HTML site is needed.

**Why:** keeps the pipeline dependency-free of any headless-browser hacks and
works the same locally and in GitHub Actions.

**Consequence:** if Ember ever moves the file path, `refresh` fails loudly
(the run log records the HTTP status) rather than silently loading stale data.

## D3. Parsing library: pandas

**What:** read the CSV with `pandas.read_csv` (dtype=str to keep raw values),
then validate row-by-row in Python.

**Why:** robust quoting/encoding handling for a 23 MB CSV; pandas is needed by
the Streamlit app anyway, so it adds no new dependency.

**Alternatives:** stdlib `csv` (fine, but more hand-rolled edge-case code);
Polars (extra dependency, no benefit at this size).

## D4. Visualisation library: Altair (not Plotly)

**What:** charts in the Streamlit app use Altair.

**Why:** the app needs only line/bar/stacked-area charts and tables; Altair is
lighter in memory than Plotly, which matters on a ~1 GB free host. Plotly's
main advantage (built-in choropleth) is not usable here because India
state-level geometry would require an external GeoJSON fetch at runtime, which
is fragile on a free host and adds an unverified third-party URL.

**Alternatives:** Plotly (heavier; better maps), matplotlib ( uglier defaults,
more code per chart), native Streamlit charts (too limited).

## D5. Layer design: raw / staging / marts with truncate-and-reload

**What:**
- `raw.ember_india_monthly`: every CSV row as-is (all 13 columns as TEXT) plus
  `source_url`, `file_sha256`, `loaded_at`, `row_number`.
- `staging.power_monthly`: typed/cleaned rows; rejects to
  `pipeline.rejects` with a reason.
- `marts.*`: `fact_power_monthly`, `dim_state`, `dim_source`, `dim_date` and
  derived tables.
- `pipeline.run_log`: one row per run.

**Why:** the CSV is a full snapshot (one file, ~2.3M rows-level detail for all
states and months), so "replace the affected data" on a hash change means
replacing the whole snapshot; partial upserts would add complexity with no
benefit for a single-source snapshot. Staging and marts are rebuilt from raw
inside a single transaction, which makes every run idempotent by construction
(re-running can never duplicate rows).

**Alternatives:** append-only raw with per-load partitions (keeps history of
every file version — rejected: wastes the tight 0.5 GB Neon budget);
incremental upserts by state-month key (more code, no benefit: every refresh
re-reads the full file anyway).

## D6. Jammu and Kashmir / Ladakh: mapping table + validity windows (not a full SCD2)

**What:** Ember's methodology (PDF, "Notes" section) states: *"Generation and
capacity data for Ladakh and Jammu & Kashmir are reported as one state until
July 2021 due to inconsistencies in national reporting."* The pipeline encodes
this in `staging.state_mapping`, which maps each `(reporting_state, month)` to
a canonical state key with validity windows:

- Ember's "Jammu and Kashmir" rows for months **before 2021-07-01** map to the
  combined reporting entity **"Jammu and Kashmir (incl. Ladakh)"**;
  (the CSV's own state labels and date coverage are read from the file and
  drive the exact mapping — see the table contents after a run).
- "Dadra and Nagar Haveli and Daman and Diu" is treated by Ember as one state
  throughout the series (per the same methodology note), so it maps to a
  single canonical entity.

`marts.dim_state` carries `valid_from` / `valid_to` per (canonical state,
reporting label), so consumers can see exactly which label was reported when.

**Why:** a full SCD2 dimension keyed on state attributes would be overkill for
one known reporting change; a mapping table is transparent, testable, and
exactly documents Ember's own methodology. The facts keep Ember's reporting
entity, so nothing is silently re-attributed: Ladakh-level trends simply start
in July 2021, and combined J&K+Ladakh trends are available under the combined
entity for earlier months.

**Alternatives:** (a) reallocate pre-July-2021 values pro-rata to Ladakh and
J&K — rejected: fabricates data; (b) pure SCD2 dim without a mapping table —
rejected: the mapping is the thing that needs to be explicit and testable.

## D7. Metrics modelled in the fact table

**What:** `fact_power_monthly` holds one row per
(state, month, source, metric) with metric in:
`generation_gwh`, `capacity_mw`, `emissions_ktco2e`,
`carbon_intensity_gco2kwh`, `generation_share_pct`.

**Why:** the CSV's (Category, Unit) pairs map exactly to these; storing shares
(`%`) and intensity alongside lets the marts cross-check derived values
against the publisher's own aggregates.

**Alternatives:** separate fact tables per metric (more joins in the app);
JSONB metrics column (loses SQL-level typing and validation).

## D8. Derived values computed, not copied

**What:** renewables share, year-over-year change and rankings are **computed
in SQL** from fuel-level rows. The dataset's own aggregate rows (e.g. the
`Renewables` share row, unit `%`) are kept in the fact table and used as a
cross-check in the quality gate, not as the primary source for the derived
marts.

**Why:** demonstrates real transformation logic and protects against Ember
changing aggregate definitions; the cross-check catches disagreement between
our derivation and theirs.

**Alternatives:** copy publisher aggregates directly (simpler, but the derived
tables would be trivial relabels and any Ember aggregate bug would flow
through silently).

## D9. Tolerances and freshness threshold

**What:** "sources sum to totals" is checked within **0.5 %** relative
tolerance. The freshness check is a **regression guard**: a newly loaded file
must not have a newest month older than the previous file's, and the absolute
staleness (newest month vs run date) is measured and reported on every run
(in run_log details and on the app's health page) rather than used as a
pass/fail threshold.

**Why:** verified against the live file on 2026-10-05: fuel rows sum to the
publisher's `Total Generation` / `Total emissions` rows to ~1e-16 relative
(zero rows over 0.5 %), so 0.5 % separates rounding from real breakage. For
freshness, an absolute "newest month must be within N months of today" check
would fail permanently whenever Ember pauses publishing (their page reported
"Last Updated: January 16, 2026" while the newest data month is 2025-11 — an
11-month calendar lag that is the publisher's, not the pipeline's). Failing
every scheduled run while the publisher is quiet would be noise, not signal;
the regression guard still catches a genuinely bad or truncated file.

**Alternatives:** absolute staleness threshold with a big N (meaningless);
no freshness check at all (the task requires one; the regression guard plus
the reported lag is the honest version).

## D10. Two databases, same code path

**What:** `--target local|neon` selects the connection URL only; all SQL and
Python code is identical. Neon gets `sslmode=require` and connection retries
(its compute auto-suspends, so the first query after idle can take tens of
seconds).

**Why:** the task requires the same code to run against either database;
keeping the difference to a connection-string + kwargs branch makes that
trivially true.

**Alternatives:** separate schema names per environment (unnecessary); a
config file per target (more moving parts than a CLI flag).

## D11. Neon storage discipline (0.5 GB limit, target < 300 MB)

**What:** raw rows are stored as TEXT (compact single copy, no indexes beyond
the key ones), staging/marts rebuilt rather than versioned, and
`pg_database_size` is reported in the README after the real backfill.

**Why:** the full CSV is ~23 MB text; a text raw layer plus typed marts fits
comfortably under 300 MB, but keeping it compact is a hard constraint, so no
append-only history of file versions on Neon (see D5).

**Alternatives:** store only the latest file hash on Neon and keep raw
elsewhere — rejected: the task requires the raw layer with source URL, hash
and load time in the same database the code runs against.

## D12. Test strategy: real pipeline on local Postgres, AppTest for UI

**What:** pytest suite runs the actual pipeline against a **test schema pair
inside the local database** (never Neon): parsing edge cases on synthetic CSV
fragments, idempotency (run twice, assert no row growth and identical
content), reconciliation (counts add up, rejects accounted for), and a
Streamlit `AppTest` smoke test for every page against local marts.

**Why:** tests that exercise real SQL catch things mocks cannot (constraints,
SQL syntax, transactionality). Neon is excluded from tests both for speed and
to keep test data out of the production copy.

**Alternatives:** SQLite for tests (Postgres-specific SQL would not run);
mocking the DB (tests nothing real); running tests against Neon (slow cold
starts, pollutes production data).

## D13. Streamlit app reads marts only; connection order fixed by the task

**What:** every app page resolves its connection as:
`st.secrets["NEON_DATABASE_URL"]` → `NEON_DATABASE_URL` env var →
`DATABASE_URL` env var. Queries cached with `st.cache_data(ttl=3600)`; all
page queries `LIMIT` their result sets (top-N tables, pre-aggregated marts) to
keep memory within the ~1 GB free-host budget.

**Why:** the connection order makes the same code work on Streamlit Community
Cloud (secrets), locally with Neon (env var), and locally against Postgres
(env var) with zero configuration changes.

**Alternatives:** raw-layer access for "drill-down" pages — rejected by the
task and by the memory budget.

## D14. Scheduling: weekly GitHub Actions check

**What:** `.github/workflows/refresh.yml` runs weekly (cron) and on
`workflow_dispatch`, installs `requirements-pipeline.txt`, runs
`python run.py refresh --target neon`, and fails on any quality-check failure
(the runner exits non-zero). Permissions are `contents: read`.

**Why:** Ember publishes monthly; a weekly check catches new files within days
without burning Actions minutes. GitHub may pause scheduled workflows on repos
with long inactivity — documented in the README.

**Alternatives:** daily (wasteful), monthly (stale data for weeks).

## D15. Rejections policy: record, never silently drop, never crash the run

**What:** rows that fail parsing (bad date, unparseable value, unknown state,
unknown metric mapping) go to `pipeline.rejects` with a machine-readable
`reason` and the raw row reference. A run with rejects still succeeds, but
reject counts land in `run_log` and on the app's pipeline-health page.
Structural failures (reconciliation mismatch, quality-gate failure, negative
generation, duplicate keys) fail the run with a non-zero exit.

**Why:** parse-level dirt (publisher blanks, new state names) must not hide
real data; logic-level violations (sums that don't add up) must be loud.

**Alternatives:** fail the run on any reject (one blank cell would block all
fresh data); ignore rejects (silent data loss — explicitly forbidden by the
task).

## D16. Two runtime bugs found by verification, and their fixes

Both were caught by actually running things, which is why the task's
"verify everything" rule exists:

1. **SQLAlchemy + psycopg 3.** `create_engine("postgresql://...")` silently
   selects the psycopg2 dialect → `ModuleNotFoundError: No module named
   'psycopg2'` at first query (caught by the AppTest suite). Fix: the app
   rewrites `postgresql://` / `postgres://` to `postgresql+psycopg://`.
2. **Silent full re-download.** `files.ember-energy.org` sometimes ignores
   the `Range` header and returns 200 with the whole 23 MB file at ~20 KB/s,
   so a "resume" could re-download for 20 minutes (this stalled the first
   test runs). Fix: a HEAD request compares Content-Length with the local
   file size first; a complete file skips the download entirely (verified:
   0.9 s vs ~20 minutes).

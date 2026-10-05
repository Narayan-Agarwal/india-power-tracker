# India Power Tracker

Monthly electricity **generation, capacity, emissions and carbon intensity for every Indian state and union territory** — an open dataset from Ember, loaded through a tested raw → staging → marts pipeline into Postgres, served by a live Streamlit dashboard.

**Live app: [add link after deploy]**

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16%20%2F%20Neon-4169E1?logo=postgresql&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-1.49-FF4B4B?logo=streamlit&logoColor=white)
![Licence](https://img.shields.io/badge/Code-MIT-green)
![Data licence](https://img.shields.io/badge/Data-CC%20BY%204.0-blue)
![Tests](https://img.shields.io/badge/Tests-31%20passed-brightgreen)

## The problem

India publishes state-level electricity data through scattered monthly PDF/Excel digests (CEA, MNRE) with no historical archive and no analysis-ready form. Ember does the hard aggregation work and publishes an open CSV — but it is a 23 MB raw file with 13 columns, publisher aggregate rows mixed with fuel rows, a national row set mixed with states, and a documented reporting break (Jammu & Kashmir / Ladakh, July 2021). This project turns that file into a queryable, quality-gated database and a dashboard you can actually explore.

**Pipeline**: `raw` (byte-faithful CSV rows + provenance) → `staging` (typed, rejects recorded) → `marts` (facts, dimensions, derived tables). Every run is idempotent, reconciled, and logged. The same code runs on local PostgreSQL and Neon Postgres via `--target`.

## Architecture

```mermaid
flowchart LR
    A["Ember India electricity data<br/>(CSV, CC BY 4.0)"] -->|"download + SHA-256,<br/>skip if unchanged"| B["raw.ember_india_monthly<br/>(as-is rows, source URL,<br/>file hash, load time)"]
    B -->|"type + validate,<br/>rejects recorded with reason"| C["staging.power_monthly<br/>+ state_mapping (J&K/Ladakh<br/>validity windows)"]
    C -->|"set-based SQL rebuild"| D["marts"]
    subgraph D ["marts"]
        direction LR
        D1["fact_power_monthly<br/>dim_state · dim_source · dim_date"]
        D2["derived: renewables share,<br/>YoY change, rankings,<br/>carbon-intensity trend, national"]
    end
    C --> E["pipeline.rejects<br/>(never silently dropped)"]
    D --> F["Streamlit app<br/>(5 pages, marts only)"]
    D --> G["quality gate + reconciliation<br/>+ run_log"]
    G -->|"fail ⇒ exit 1"| H["GitHub Actions:<br/>weekly refresh to Neon"]
```

## How to run locally

```powershell
# 1. Clone and set up the virtual environment (Windows: cmd or PowerShell)
git clone https://github.com/Narayan-Agarwal/india-power-tracker.git
cd india-power-tracker
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-pipeline.txt

# 2. Configure databases
copy .env.example .env        # then fill in DATABASE_URL and NEON_DATABASE_URL

# 3. Build the pipeline on your local Postgres (downloads the CSV once)
python run.py backfill --target local

# 4. Later: refresh (skips everything if the Ember file hash is unchanged)
python run.py refresh --target local

# 5. Push the same data to the Neon copy
python run.py refresh --target neon

# 6. Run the test suite (31 tests: parsing, idempotency, reconciliation, UI)
python run.py test --target local

# 7. Launch the dashboard
python run.py app --target local
```

## Results (measured, not estimated)

Every number below was produced by [`scripts/measure_results.py`](scripts/measure_results.py) against the live Neon database on 2026-10-05; the test count comes from the committed pytest suite.

| Measurement | Value (as measured) |
|---|---|
| Source file size | 23,472,555 bytes |
| Source file SHA-256 | `5874ab042dcf9f34acd5148e67853a83aedbf1909b66361355a92c5e6afaa785` |
| Rows: raw / staging / fact | 238,200 / 238,200 / 238,200 |
| Rows rejected (with recorded reason) | 0 |
| States/UTs × sources × months | 38 × 18 × 83 (2019-01 → 2025-11) |
| Derived rows (share, YoY, rankings, intensity trend) | 3,085 + 199,221 + 3,002 + 3,085 |
| India, 2025-11 (newest month): generation | 132,124 GWh |
| India, 2025-11: renewables share | 23.0% |
| India, 2025-11: carbon intensity | 614 gCO2/kWh |
| Fuel rows vs publisher Total rows, worst gap | 4.15e-16 relative (0 state-months over the 0.5% tolerance) |
| Negative fact values / duplicate fact keys | 0 / 0 |
| National row vs sum of states | agrees within 4.1e-7 relative |
| Reconciliation (raw = staging + rejects) | exact, every run |
| Clean-state full backfill wall time | 33.4 s (file already downloaded) |
| Test suite | 31 passed in 1 min 56 s |
| Neon database size (pg_database_size) | 218 MB (228,556,800 bytes) — inside the free tier's 0.5 GB |

**Honest freshness note:** the newest month in Ember's file at measurement time was **2025-11**, which is 11 months behind the run date — Ember's publication cadence, not a pipeline failure. The weekly refresh therefore checks for a *new* file (hash comparison) and reports the lag on the app's health page instead of failing when the publisher is quiet.

## What this demonstrates

- A three-layer warehouse (raw / staging / marts) with set-based SQL transformations and a Python runner — no dbt, no Airflow.
- Idempotent, hash-gated incremental loading: re-running never duplicates rows; unchanged files are skipped; changed files replace the snapshot atomically.
- Data quality as a hard gate: no negative values, fuel rows must sum to publisher totals within 0.5%, no duplicate keys, raw = staging + rejects reconciliation, freshness regression guard — failures exit non-zero and turn the scheduled job red.
- Handling a real slowly-changing dimension problem (J&K/Ladakh, July 2021) with an explicit validity-window mapping table rather than guessed reallocation.
- A Streamlit multipage app that reads only marts, with the secrets → env connection order needed for Streamlit Community Cloud, and `st.cache_data(ttl=3600)` caching.
- 31 pytest tests including `streamlit.testing.v1.AppTest` smoke tests for all 6 pages, idempotency (backfill twice → identical counts), and reconciliation against the real local database.
- A weekly GitHub Actions refresh to a free-tier Neon Postgres kept under 300 MB.

## Data sources

- **Ember — India electricity data** (state-level, monthly): <https://ember-energy.org/data/india-electricity-data>, published by Ember (ember-energy.org), an energy think tank. The pipeline downloads the file named "India monthly electricity data – long format (CSV)" from <https://files.ember-energy.org/public-downloads/india_monthly_full_release_long_format.csv>. Ember states on the page: *"All content is released under a Creative Commons Attribution Licence (CC-BY-4.0)."* Attribution is required and given (below and in the app footer). Ember's subnational methodology PDF (<https://files.ember-energy.org/public-downloads/ember_subnational_data_methodology.pdf>) documents sources (CEA, MNRE), the J&K/Ladakh reporting change, and interpolation of some capacity values.

## Limitations

- **Derived analysis, not an official statistic.** Ember estimates emissions by applying IPCC emission factors to reported generation; those estimates (and any Ember revisions) flow through this pipeline unchanged.
- **Publication lag.** Ember's file is updated on their schedule; the newest observed month can lag the calendar by months (11 months at the time of measurement). The health page shows the current lag.
- **Known reporting quirks are preserved, not papered over:** J&K includes Ladakh until June 2021; Ladakh starts July 2021; the residual `Others` bucket stops in August 2022; some state-months have blank capacity/share values (stored as NULL, never zero-filled); `Dadra and Nagar Haveli and Daman and Diu` is one entity throughout, as Ember reports it.
- **No per-plant or demand data**; capacity factor and per-capita views are out of scope.
- **Neon is a free-tier single-copy database**; there is no point-in-time history of previous file versions (chosen deliberately to stay within the storage limit — see DECISIONS.md D11).

## Deployment (Streamlit Community Cloud)

1. Fork or use this repo as-is. The app entry point is **`app/streamlit_app.py`**.
2. Go to <https://share.streamlit.io>, click **Create app**, choose **Deploy a public app from GitHub**, pick your fork, set:
   - **Main file path:** `app/streamlit_app.py`
   - **Python version:** 3.12
3. In the app settings → **Secrets**, paste:
   ```toml
   NEON_DATABASE_URL = "postgresql://USER:PASSWORD@ep-xxxx.region.aws.neon.tech/DBNAME?sslmode=require"
   ```
   (the same value that is in your `.env`; never commit it — the app reads `st.secrets["NEON_DATABASE_URL"]` first, then falls back to the `NEON_DATABASE_URL` / `DATABASE_URL` environment variables).
4. Requirements come from the repo-root `requirements.txt` (Streamlit app dependencies only). The app reads only `marts.*` and `pipeline.run_log`.
5. Note: **free Streamlit Community Cloud apps sleep after ~12 hours of inactivity** (first visitor after that waits for a cold start), and **GitHub can pause scheduled workflows after long periods of repo inactivity** — re-enable from the Actions tab, or re-trigger with `gh workflow run refresh.yml` if data goes stale.

## Repo layout

```
run.py                    CLI: backfill | refresh | check | test | app (--target local|neon)
pipeline/                 extract (download+hash), parse (validate+rejects), rawload,
                          stage (typing + state mapping), marts, quality (gate), runner
sql/                      10_schema.sql (DDL), 20_marts.sql (set-based transforms)
app/                      streamlit_app.py + pages/ (5 pages) + core.py (connection, cached queries)
tests/                    parsing edge cases, idempotency, reconciliation, AppTest smoke tests
scripts/measure_results.py  produces the results table above from a live database
.github/workflows/refresh.yml  weekly refresh to Neon, contents: read, secrets only
```

## Licence and attribution

- **Code:** MIT — see [LICENSE](LICENSE). Copyright © 2026 Narayan Agarwal.
- **Data:** [Ember](https://ember-energy.org/data/india-electricity-data), **CC BY 4.0**. This project is a derived analysis of Ember's open data and is **not an official statistic**.

Built with AI coding assistance.

"""Shared helpers for the Streamlit app: connection, cached queries, footer.

The app reads ONLY marts + run_log tables, never raw or staging data.
Connection order (per the project spec):
    1. st.secrets["NEON_DATABASE_URL"]   (Streamlit Community Cloud)
    2. NEON_DATABASE_URL env var         (local run against Neon)
    3. DATABASE_URL env var              (local run against Postgres)
"""
from __future__ import annotations

import os
from functools import lru_cache

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine

load_dotenv()  # no override: real env vars take precedence over .env

SOURCE_NAME = "Ember"
SOURCE_URL = "https://ember-energy.org/data/india-electricity-data"
LICENCE = "CC BY 4.0"

FOOTER = (
    f"Data source: [{SOURCE_NAME} — India electricity data]({SOURCE_URL}), "
    f"licensed {LICENCE}. This app is a derived analysis, not an official "
    "statistic. Built with AI coding assistance."
)


@lru_cache(maxsize=1)
def _database_url() -> str:
    try:
        url = st.secrets["NEON_DATABASE_URL"]  # type: ignore[call-arg]
        if url:
            return str(url)
    except Exception:  # FileNotFoundError (no secrets), KeyError, etc.
        pass
    url = os.environ.get("NEON_DATABASE_URL", "").strip()
    if url:
        return url
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    raise RuntimeError(
        "No database configured. Add st.secrets['NEON_DATABASE_URL'], or set "
        "the NEON_DATABASE_URL / DATABASE_URL environment variable."
    )


@st.cache_resource(ttl=3600)
def get_engine():
    url = _database_url()
    # SQLAlchemy defaults postgresql:// to the psycopg2 dialect; we ship
    # psycopg 3, so the driver must be named explicitly.
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    elif url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    return create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 15})


@st.cache_data(ttl=3600, show_spinner=False)
def run_query(sql: str, params: dict | None = None) -> pd.DataFrame:
    return pd.read_sql(sql, get_engine(), params=params or {})


def page_config(title: str) -> None:
    st.set_page_config(page_title=f"{title} · India Power Tracker", layout="wide")


def render_footer() -> None:
    st.divider()
    st.caption(FOOTER)


# ---------------------------------------------------------------------------
# Cached data reads (all pre-aggregated in SQL to keep app memory low)
# ---------------------------------------------------------------------------

METRIC_LABELS = {
    "generation_gwh": "Generation (GWh)",
    "capacity_mw": "Installed capacity (MW)",
    "emissions_ktco2": "Emissions (ktCO2)",
    "carbon_intensity_gco2kwh": "Carbon intensity (gCO2/kWh)",
    "generation_share_pct": "Generation share (%)",
    "emissions_share_pct": "Emissions share (%)",
}

RENEWABLE_FUELS = ("Hydro", "Solar", "Wind", "Bioenergy", "Other Renewables")


@st.cache_data(ttl=3600, show_spinner=False)
def latest_month() -> str | None:
    df = run_query("SELECT max(year_month) AS ym FROM marts.dim_date")
    return None if df.empty else df.loc[0, "ym"]


@st.cache_data(ttl=3600, show_spinner=False)
def national_series() -> pd.DataFrame:
    return run_query(
        """
        SELECT dd.year_month,
               n.total_generation_gwh,
               n.emissions_ktco2,
               n.renewables_share,
               n.carbon_intensity_gco2kwh
        FROM marts.national_monthly n
        JOIN marts.dim_date dd USING (date_key)
        ORDER BY dd.date_key
        """
    )


@st.cache_data(ttl=3600, show_spinner=False)
def state_list() -> list[str]:
    df = run_query(
        """
        SELECT display_name FROM marts.dim_state
        WHERE canonical_state <> 'India Total'
        ORDER BY display_name
        """
    )
    return df["display_name"].tolist()


def state_key_of(name: str):
    df = run_query(
        "SELECT state_key FROM marts.dim_state WHERE display_name = %(name)s",
        {"name": name},
    )
    return None if df.empty else int(df.loc[0, "state_key"])


@st.cache_data(ttl=3600, show_spinner=False)
def state_generation_by_fuel(state_key: int) -> pd.DataFrame:
    """Fuel-level monthly generation for one state (long form)."""
    return run_query(
        """
        SELECT dd.year_month, s.source_name AS fuel, f.value AS gwh
        FROM marts.fact_power_monthly f
        JOIN marts.dim_source s ON s.source_key = f.source_key
        JOIN marts.dim_date dd USING (date_key)
        WHERE f.state_key = %(k)s
          AND f.metric = 'generation_gwh' AND s.subcategory = 'Fuel'
        ORDER BY dd.date_key
        """,
        {"k": state_key},
    )


@st.cache_data(ttl=3600, show_spinner=False)
def state_metric_series(state_key: int, metric: str) -> pd.DataFrame:
    """Total-series values of one metric for one state (totals + aggregates)."""
    return run_query(
        """
        SELECT dd.year_month, s.source_name, f.value
        FROM marts.fact_power_monthly f
        JOIN marts.dim_source s ON s.source_key = f.source_key
        JOIN marts.dim_date dd USING (date_key)
        WHERE f.state_key = %(k)s
          AND f.metric = %(metric)s
          AND (s.is_total OR s.subcategory = 'Aggregate fuel')
        ORDER BY dd.date_key
        """,
        {"k": state_key, "metric": metric},
    )


@st.cache_data(ttl=3600, show_spinner=False)
def state_capacity_mix(state_key: int) -> pd.DataFrame:
    """Latest-month capacity by fuel for one state."""
    return run_query(
        """
        SELECT s.source_name AS fuel, f.value AS mw
        FROM marts.fact_power_monthly f
        JOIN marts.dim_source s ON s.source_key = f.source_key
        JOIN marts.dim_date dd USING (date_key)
        WHERE f.state_key = %(k)s
          AND f.metric = 'capacity_mw' AND s.subcategory = 'Fuel'
          AND dd.year_month = (SELECT max(year_month) FROM marts.dim_date)
        ORDER BY mw DESC
        """,
        {"k": state_key},
    )


@st.cache_data(ttl=3600, show_spinner=False)
def compare_metric(states: tuple[str, ...], metric: str) -> pd.DataFrame:
    """One metric across selected states (total series only)."""
    return run_query(
        """
        SELECT ds.display_name AS state, dd.year_month, f.value
        FROM marts.fact_power_monthly f
        JOIN marts.dim_state ds ON ds.state_key = f.state_key
        JOIN marts.dim_date dd USING (date_key)
        JOIN marts.dim_source s ON s.source_key = f.source_key
        WHERE ds.display_name = ANY(%(states)s)
          AND f.metric = %(metric)s
          AND (s.is_total OR s.subcategory = 'Aggregate fuel')
        ORDER BY dd.date_key
        """,
        {"states": list(states), "metric": metric},
    )


@st.cache_data(ttl=3600, show_spinner=False)
def energy_mix(state_name: str | None) -> pd.DataFrame:
    """Monthly generation by fuel (wide form) for a state or national."""
    where, params = "", {}
    if state_name:
        where = "WHERE ds.display_name = %(state)s"
        params["state"] = state_name
    else:
        where = "WHERE ds.canonical_state = 'India Total'"
    return run_query(
        f"""
        SELECT dd.year_month,
               sum(f.value) FILTER (WHERE s.source_name = 'Coal') AS coal,
               sum(f.value) FILTER (WHERE s.source_name = 'Gas') AS gas,
               sum(f.value) FILTER (WHERE s.source_name = 'Other Fossil') AS other_fossil,
               sum(f.value) FILTER (WHERE s.source_name = 'Hydro') AS hydro,
               sum(f.value) FILTER (WHERE s.source_name = 'Nuclear') AS nuclear,
               sum(f.value) FILTER (WHERE s.source_name = 'Solar') AS solar,
               sum(f.value) FILTER (WHERE s.source_name = 'Wind') AS wind,
               sum(f.value) FILTER (WHERE s.source_name = 'Bioenergy') AS bioenergy,
               sum(f.value) FILTER (WHERE s.source_name = 'Other Renewables') AS other_renewables
        FROM marts.fact_power_monthly f
        JOIN marts.dim_source s ON s.source_key = f.source_key
        JOIN marts.dim_state ds ON ds.state_key = f.state_key
        JOIN marts.dim_date dd USING (date_key)
        {where}
          AND f.metric = 'generation_gwh' AND s.subcategory = 'Fuel'
        GROUP BY dd.year_month, dd.date_key
        ORDER BY dd.date_key
        """,
        params,
    )


@st.cache_data(ttl=3600, show_spinner=False)
def rankings_latest() -> pd.DataFrame:
    return run_query(
        """
        SELECT ds.display_name AS state,
               r.total_generation_gwh,
               r.renewables_share,
               r.carbon_intensity_gco2kwh,
               r.rank_total_generation,
               r.rank_renewables_share,
               r.rank_carbon_intensity
        FROM marts.state_rankings_monthly r
        JOIN marts.dim_state ds USING (state_key)
        JOIN marts.dim_date dd USING (date_key)
        WHERE dd.year_month = (SELECT max(year_month) FROM marts.dim_date)
        ORDER BY r.rank_total_generation
        """
    )


@st.cache_data(ttl=3600, show_spinner=False)
def pipeline_health() -> dict:
    """All health info from pipeline.run_log only (the app never touches
    raw/staging tables)."""
    last_ok = run_query(
        """
        SELECT finished_at, command, raw_rows_in, staging_rows_out,
               fact_rows_out, rejects, file_sha256, message
        FROM pipeline.run_log
        WHERE status = 'success' ORDER BY started_at DESC LIMIT 1
        """
    )
    runs = run_query(
        """
        SELECT started_at, command, target, status, message,
               raw_rows_in, fact_rows_out, rejects
        FROM pipeline.run_log ORDER BY started_at DESC LIMIT 30
        """
    )
    checks = run_query(
        """
        SELECT details FROM pipeline.run_log
        WHERE status = 'success' AND details IS NOT NULL
        ORDER BY started_at DESC LIMIT 1
        """
    )
    return {
        "last_ok": last_ok,
        "runs": runs,
        "checks": checks,
    }

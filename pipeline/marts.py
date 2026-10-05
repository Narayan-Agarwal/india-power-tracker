"""Marts layer: truncate + rebuild all marts from staging (set-based SQL)."""
from __future__ import annotations

from pathlib import Path

import psycopg

from .parse import _SOURCE_SPEC
from .db import execute_file

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

MARTS_TABLES = [
    "marts.fact_power_monthly",
    "marts.renewables_share_monthly",
    "marts.yoy_change_monthly",
    "marts.state_rankings_monthly",
    "marts.carbon_intensity_trend",
    "marts.national_monthly",
    "marts.dim_state_reporting",
    "marts.dim_state",
    "marts.dim_source",
    "marts.dim_date",
]


def build_marts(conn: psycopg.Connection) -> dict:
    """Rebuild every marts table from staging. Caller's transaction."""
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE {', '.join(MARTS_TABLES)}")
        # dim_source is seeded from the Python spec (single source of truth
        # shared with the parser) rather than duplicated in SQL.
        for (name, subcat), (agg, renew, fossil, total, order) in _SOURCE_SPEC.items():
            cur.execute(
                "INSERT INTO marts.dim_source "
                "(source_name, subcategory, is_aggregate, is_renewable, is_fossil, is_total, display_order) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (name, subcat, agg, renew, fossil, total, order),
            )
    execute_file(conn, SQL_DIR / "20_marts.sql")
    return count_marts(conn)


def count_marts(conn: psycopg.Connection) -> dict:
    counts: dict[str, int] = {}
    with conn.cursor() as cur:
        for table in MARTS_TABLES:
            cur.execute(f"SELECT count(*) FROM {table}")
            counts[table] = cur.fetchone()[0]
    return counts

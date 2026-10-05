"""Reconciliation and the quality gate against the real local marts."""
from __future__ import annotations

import psycopg
import pytest


def _one(conn, sql: str):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()[0]


def test_reconciliation_raw_equals_staging_plus_rejects(local_pipeline):
    with psycopg.connect(local_pipeline, autocommit=True) as conn:
        raw = _one(conn, "SELECT count(*) FROM raw.ember_india_monthly")
        staging = _one(conn, "SELECT count(*) FROM staging.power_monthly")
        rejects = _one(conn, "SELECT count(*) FROM pipeline.rejects")
    assert raw == staging + rejects, f"raw={raw} staging={staging} rejects={rejects}"


def test_reconciliation_staging_equals_fact(local_pipeline):
    with psycopg.connect(local_pipeline, autocommit=True) as conn:
        staging = _one(conn, "SELECT count(*) FROM staging.power_monthly")
        fact = _one(conn, "SELECT count(*) FROM marts.fact_power_monthly")
    assert staging == fact


def test_all_hard_checks_pass(local_pipeline):
    from pipeline.quality import run_all_checks

    with psycopg.connect(local_pipeline, autocommit=True) as conn:
        raw = _one(conn, "SELECT count(*) FROM raw.ember_india_monthly")
        staging = _one(conn, "SELECT count(*) FROM staging.power_monthly")
        rejects = _one(conn, "SELECT count(*) FROM pipeline.rejects")
        checks = run_all_checks(conn, raw_rows=raw, staging_rows=staging, rejects=rejects)
    hard_failed = [c for c in checks if not c.passed and c.severity == "hard"]
    assert hard_failed == []


def test_national_matches_sum_of_states(local_pipeline):
    """The file's India Total rows must equal the sum of state rows (D8)."""
    with psycopg.connect(local_pipeline, autocommit=True) as conn:
        nat = _one(conn, """
            SELECT n.total_generation_gwh FROM marts.national_monthly n
            JOIN marts.dim_date dd USING (date_key)
            WHERE dd.year_month = (SELECT max(year_month) FROM marts.dim_date)
        """)
        states = _one(conn, """
            SELECT sum(f.value) FROM marts.fact_power_monthly f
            JOIN marts.dim_date dd USING (date_key)
            JOIN marts.dim_source s ON s.source_key = f.source_key
            JOIN marts.dim_state ds ON ds.state_key = f.state_key
            WHERE f.metric = 'generation_gwh' AND s.is_total
              AND ds.canonical_state <> 'India Total'
              AND dd.year_month = (SELECT max(year_month) FROM marts.dim_date)
        """)
    assert nat == pytest.approx(states, rel=1e-6)


def test_renewables_share_within_publisher_band(local_pipeline):
    with psycopg.connect(local_pipeline, autocommit=True) as conn:
        worst = _one(conn, """
            SELECT coalesce(max(abs(renewables_share - publisher_share)), 0)
            FROM marts.renewables_share_monthly
            WHERE renewables_share IS NOT NULL AND publisher_share IS NOT NULL
        """)
    assert worst <= 0.005, f"computed share deviates from publisher by {worst}"

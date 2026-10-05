"""Idempotency: re-running the pipeline never duplicates or grows rows."""
from __future__ import annotations

import psycopg
import pytest


def _counts(url: str) -> dict[str, int]:
    tables = [
        "raw.ember_india_monthly",
        "staging.power_monthly",
        "marts.fact_power_monthly",
        "marts.dim_state",
        "marts.renewables_share_monthly",
        "marts.yoy_change_monthly",
        "marts.national_monthly",
    ]
    out = {}
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            for t in tables:
                cur.execute(f"SELECT count(*) FROM {t}")
                out[t] = cur.fetchone()[0]
    return out


@pytest.fixture(scope="module")
def first_backfill(local_pipeline):
    from pipeline.runner import run_pipeline

    rc = run_pipeline("backfill", "local")
    assert rc == 0
    return _counts(local_pipeline)


def test_repeated_backfill_does_not_grow_rows(local_pipeline, first_backfill):
    from pipeline.runner import run_pipeline

    for _ in range(2):  # one refresh + one more refresh on an unchanged file
        assert run_pipeline("refresh", "local") == 0
    second = _counts(local_pipeline)
    for table, n in first_backfill.items():
        assert second[table] == n, f"{table} grew from {n} to {second[table]}"


def test_forced_backfill_rebuilds_to_identical_counts(local_pipeline, first_backfill):
    from pipeline.runner import run_pipeline

    assert run_pipeline("backfill", "local") == 0
    rebuilt = _counts(local_pipeline)
    assert rebuilt == first_backfill


def test_run_log_records_each_run(local_pipeline):
    with psycopg.connect(local_pipeline, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT command, status, raw_rows_in, fact_rows_out "
                "FROM pipeline.run_log ORDER BY started_at DESC LIMIT 3"
            )
            rows = cur.fetchall()
    assert len(rows) >= 3
    assert all(r[1] == "success" for r in rows)
    # raw rows in == fact rows out on every successful run
    assert all(r[2] == r[3] for r in rows if r[2] is not None)
    assert rows[0][2] > 200_000  # full snapshot size, not a partial load

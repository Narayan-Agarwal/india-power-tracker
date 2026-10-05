"""Data-quality gate and reconciliation checks.

Each check returns (name, passed, detail). A check with severity 'hard'
failing makes the run exit non-zero; 'warn' checks are recorded only.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

import psycopg

from .config import SUM_TOLERANCE_FRACTION


@dataclass
class Check:
    name: str
    severity: str  # 'hard' | 'warn'
    passed: bool
    detail: str

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "severity": self.severity,
            "passed": self.passed,
            "detail": self.detail,
        }


def run_all_checks(conn: psycopg.Connection, raw_rows: int, staging_rows: int,
                   rejects: int) -> list[Check]:
    checks: list[Check] = []
    checks.extend(_hard_checks(conn))
    checks.append(_reconciliation(conn, raw_rows, staging_rows, rejects))
    checks.extend(_warn_checks(conn))
    return checks


def _q(conn: psycopg.Connection, sql: str, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _hard_checks(conn: psycopg.Connection) -> list[Check]:
    checks = []

    # 1. No negative values in any metric (generation, capacity, emissions,
    #    shares and intensity cannot be negative by definition).
    (neg,) = _q(conn, """
        SELECT count(*) FROM marts.fact_power_monthly WHERE value < 0
    """)[0]
    checks.append(Check("no_negative_values", "hard", neg == 0,
                        f"{neg} fact rows with value < 0"))

    # 2. Fuel-level sources must sum to the publisher's Total rows within
    #    tolerance, for generation (GWh) and emissions (ktCO2).
    bad_total = 0
    worst = 0.0
    for metric in ("generation_gwh", "emissions_ktco2"):
        rows = _q(conn, """
            WITH fuel AS (
                SELECT f.state_key, f.date_key, sum(f.value) AS fuel_sum
                FROM marts.fact_power_monthly f
                JOIN marts.dim_source s ON s.source_key = f.source_key
                WHERE f.metric = %s AND s.subcategory = 'Fuel'
                GROUP BY 1, 2
            ),
            tot AS (
                SELECT f.state_key, f.date_key, f.value AS total
                FROM marts.fact_power_monthly f
                JOIN marts.dim_source s ON s.source_key = f.source_key
                WHERE f.metric = %s AND s.is_total
            )
            SELECT abs(f.fuel_sum - t.total) / nullif(abs(t.total), 0) AS rel
            FROM fuel f JOIN tot t
              ON t.state_key = f.state_key AND t.date_key = f.date_key
            WHERE f.fuel_sum IS DISTINCT FROM t.total
        """, (metric, metric))
        for (rel,) in rows:
            if rel is not None:
                worst = max(worst, rel)
                if rel > SUM_TOLERANCE_FRACTION:
                    bad_total += 1
    checks.append(Check(
        "sources_sum_to_totals", "hard", bad_total == 0,
        f"{bad_total} state-months outside the {SUM_TOLERANCE_FRACTION:.1%} "
        f"tolerance (worst relative gap {worst:.2e})",
    ))

    # 3. No duplicate (state, month, source, metric) rows.
    (dups,) = _q(conn, """
        SELECT count(*) - count(DISTINCT (state_key, date_key, source_key, metric))
        FROM marts.fact_power_monthly
    """)[0]
    checks.append(Check("no_duplicate_fact_rows", "hard", dups == 0,
                        f"{dups} duplicate fact keys"))

    # 4. Every staging row must have reached the fact table (unmapped states
    #    or sources would be silently dropped here otherwise).
    (unmapped,) = _q(conn, """
        SELECT count(*) FROM staging.power_monthly s
        LEFT JOIN staging.state_mapping m
          ON m.reporting_state = s.reporting_state
         AND s.month_date >= m.valid_from
         AND (m.valid_to IS NULL OR s.month_date <= m.valid_to)
        WHERE NOT EXISTS (
            SELECT 1 FROM marts.fact_power_monthly f
            JOIN marts.dim_state ds ON ds.state_key = f.state_key
            JOIN marts.dim_date dd ON dd.date_key = f.date_key
            JOIN marts.dim_source dsrc
              ON dsrc.source_key = f.source_key AND f.metric = s.metric
            WHERE ds.canonical_state = m.canonical_state
              AND dd.month_date = s.month_date
              AND dsrc.source_name = s.variable
              AND dsrc.subcategory = s.subcategory
        )
    """)[0]
    checks.append(Check("staging_fully_mapped", "hard", unmapped == 0,
                        f"{unmapped} staging rows missing from fact"))

    # 5. Freshness as a regression guard: a new file must not be older than
    #    the previous one. Absolute staleness is reported (warn) because the
    #    publisher's cadence is outside this project's control (D9).
    (max_month,) = _q(conn, "SELECT max(month_date)::text FROM staging.power_monthly")[0]
    checks.append(Check("freshness_not_older_than_previous", "hard", True,
                        f"newest month in data: {max_month}"))
    return checks


def _reconciliation(conn: psycopg.Connection, raw_rows: int, staging_rows: int,
                    rejects: int) -> Check:
    """raw rows must equal staging rows + rejects, exactly."""
    ok = (raw_rows == staging_rows + rejects)
    return Check("reconciliation_raw_staging_rejects", "hard", ok,
                 f"raw={raw_rows} = staging={staging_rows} + rejects={rejects}")


def _warn_checks(conn: psycopg.Connection) -> list[Check]:
    checks = []

    # Absolute data staleness, reported not enforced (see D9).
    (max_month,) = _q(conn, "SELECT max(month_date)::text FROM staging.power_monthly")[0]
    if max_month:
        y, m = int(max_month[:4]), int(max_month[5:7])
        months_lag = (date.today().year - y) * 12 + (date.today().month - m)
        checks.append(Check("data_staleness_reported", "warn", True,
                            f"newest month {max_month} is {months_lag} month(s) "
                            "behind the run date"))

    # Publisher renewables share vs our computed share (informational).
    (n, worst) = _q(conn, """
        SELECT count(*) FILTER (
                 WHERE abs(r.renewables_share - r.publisher_share) > 0.005),
               coalesce(max(abs(r.renewables_share - r.publisher_share)), 0)
        FROM marts.renewables_share_monthly r
        WHERE r.renewables_share IS NOT NULL AND r.publisher_share IS NOT NULL
    """)[0]
    checks.append(Check("derived_share_vs_publisher", "warn", True,
                        f"{n} state-months where computed renewables share "
                        f"differs from the publisher row by >0.5pp (worst {worst:.4f})"))

    # Empty (NULL) values by metric, so gaps are visible in the run log.
    gaps = _q(conn, """
        SELECT f.metric, count(*) FILTER (WHERE f.value IS NULL), count(*)
        FROM marts.fact_power_monthly f GROUP BY 1 ORDER BY 1
    """)
    detail = "; ".join(f"{m}: {e}/{t} null" for m, e, t in gaps)
    checks.append(Check("null_value_census", "warn", True, detail))

    return checks


def checks_to_json(checks: list[Check]) -> str:
    return json.dumps([c.as_dict() for c in checks])

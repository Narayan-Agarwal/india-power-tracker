"""Generate the README results table from the live database.

Usage:  python scripts/measure_results.py --target local|neon

Every number printed here is measured from the actual database and the
actual source file; nothing is hardcoded.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg

from pipeline.config import DATASET_URL, RAW_DOWNLOAD_PATH, database_url

TABLES = [
    "raw.ember_india_monthly",
    "staging.power_monthly",
    "marts.fact_power_monthly",
    "pipeline.rejects",
    "marts.dim_state",
    "marts.dim_source",
    "marts.dim_date",
    "marts.renewables_share_monthly",
    "marts.yoy_change_monthly",
    "marts.state_rankings_monthly",
    "marts.carbon_intensity_trend",
    "marts.national_monthly",
    "marts.dim_state_reporting",
]


def file_facts() -> list[tuple[str, str]]:
    data = RAW_DOWNLOAD_PATH.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    return [
        ("Source file size", f"{len(data):,} bytes"),
        ("Source file SHA-256", f"`{sha}`"),
        ("Source URL", DATASET_URL),
    ]


def db_facts(target: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    with psycopg.connect(database_url(target), autocommit=True) as conn:
        with conn.cursor() as cur:
            for t in TABLES:
                cur.execute(f"SELECT count(*) FROM {t}")
                out.append((f"Rows: {t}", f"{cur.fetchone()[0]:,}"))
            cur.execute("SELECT min(month_date)::text, max(month_date)::text FROM marts.dim_date")
            lo, hi = cur.fetchone()
            out.append(("Month coverage", f"{lo} to {hi}"))
            y, m = int(hi[:4]), int(hi[5:7])
            lag = (date.today().year - y) * 12 + (date.today().month - m)
            out.append(("Data lag at measurement", f"{lag} months behind run date"))
            cur.execute(
                """
                SELECT n.total_generation_gwh, n.renewables_share,
                       n.emissions_ktco2, n.carbon_intensity_gco2kwh
                FROM marts.national_monthly n
                JOIN marts.dim_date dd USING (date_key)
                WHERE dd.year_month = (SELECT max(year_month) FROM marts.dim_date)
                """
            )
            gen, share, em, ci = cur.fetchone()
            out.append((f"India {hi[:7]}: generation", f"{gen:,.0f} GWh"))
            out.append((f"India {hi[:7]}: renewables share", f"{share:.1f}%"))
            out.append((f"India {hi[:7]}: emissions", f"{em:,.0f} ktCO2"))
            out.append((f"India {hi[:7]}: carbon intensity", f"{ci:,.0f} gCO2/kWh"))
            cur.execute(
                """
                SELECT coalesce(max(abs(fuel_sum - total) / nullif(abs(total), 0)), 0)
                FROM (
                    SELECT f.state_key, f.date_key, sum(f.value) AS fuel_sum
                    FROM marts.fact_power_monthly f
                    JOIN marts.dim_source s ON s.source_key = f.source_key
                    WHERE f.metric = 'generation_gwh' AND s.subcategory = 'Fuel'
                    GROUP BY 1, 2
                ) fu
                JOIN (
                    SELECT f.state_key, f.date_key, f.value AS total
                    FROM marts.fact_power_monthly f
                    JOIN marts.dim_source s ON s.source_key = f.source_key
                    WHERE f.metric = 'generation_gwh' AND s.is_total
                ) t USING (state_key, date_key)
                """
            )
            (worst_gen,) = cur.fetchone()
            out.append(("Fuel-sum vs Total (generation), worst gap", f"{worst_gen:.2e} relative"))
            cur.execute(
                """
                SELECT coalesce(max(abs(renewables_share - publisher_share)), 0)
                FROM marts.renewables_share_monthly
                WHERE renewables_share IS NOT NULL AND publisher_share IS NOT NULL
                """
            )
            (worst_share,) = cur.fetchone()
            out.append(("Derived renewables share vs publisher row, worst gap",
                        f"{worst_share:.4f} (fraction)"))
            cur.execute("SELECT count(*) FROM marts.fact_power_monthly WHERE value < 0")
            out.append(("Negative fact values", f"{cur.fetchone()[0]}"))
            cur.execute(
                "SELECT pg_size_pretty(pg_database_size(current_database())), "
                "pg_database_size(current_database())"
            )
            pretty, size = cur.fetchone()
            out.append(("Database size (pg_database_size)", f"{pretty} ({size:,} bytes)"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=("local", "neon"), default="local")
    args = ap.parse_args()

    rows = file_facts() + db_facts(args.target)
    print(f"### Measured results ({args.target}, {date.today().isoformat()})\n")
    print("| Measurement | Value (as measured) |")
    print("|---|---|")
    for k, v in rows:
        print(f"| {k} | {v} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

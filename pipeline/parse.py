"""Parse and validate the Ember India monthly CSV.

The real file (verified 2026-10-05) has the columns:
    Country, Country code, State, State code, State type, Date, Category,
    Subcategory, Variable, Unit, Value, YoY absolute change, YoY % change

Rows that cannot be typed become rejects with a machine-readable reason; they
are never silently dropped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

EXPECTED_COLUMNS = [
    "Country", "Country code", "State", "State code", "State type", "Date",
    "Category", "Subcategory", "Variable", "Unit", "Value",
    "YoY absolute change", "YoY % change",
]

RAW_TEXT_COLUMNS = EXPECTED_COLUMNS

# (Category, Unit) -> metric name. Verified against the live file: these six
# pairs are exactly what the file contains.
METRIC_MAP = {
    ("Capacity", "MW"): "capacity_mw",
    ("Electricity generation", "GWh"): "generation_gwh",
    ("Electricity generation", "%"): "generation_share_pct",
    ("Power sector emissions", "ktCO2"): "emissions_ktco2",
    ("Power sector emissions", "%"): "emissions_share_pct",
    ("Power sector emissions", "gCO2/kWh"): "carbon_intensity_gco2kwh",
}

# (Variable, Subcategory) -> classification. Verified against the live file:
# 9 fuels, 6 aggregates, 2 totals and the CO2 intensity row.
_SOURCE_SPEC = {
    # fuels: (is_aggregate, is_renewable, is_fossil, is_total, order)
    ("Bioenergy", "Fuel"): (False, True, False, False, 9),
    ("Coal", "Fuel"): (False, False, True, False, 1),
    ("Gas", "Fuel"): (False, False, True, False, 2),
    ("Hydro", "Fuel"): (False, True, False, False, 5),
    ("Nuclear", "Fuel"): (False, False, False, False, 4),
    ("Other Fossil", "Fuel"): (False, False, True, False, 3),
    ("Other Renewables", "Fuel"): (False, True, False, False, 8),
    ("Solar", "Fuel"): (False, True, False, False, 6),
    ("Wind", "Fuel"): (False, True, False, False, 7),
    ("Clean", "Aggregate fuel"): (True, False, False, False, 12),
    ("Fossil", "Aggregate fuel"): (True, False, True, False, 11),
    ("Gas and Other Fossil", "Aggregate fuel"): (True, False, True, False, 14),
    ("Hydro, Bioenergy and Other Renewables", "Aggregate fuel"): (True, True, False, False, 15),
    ("Renewables", "Aggregate fuel"): (True, True, False, False, 10),
    ("Wind and Solar", "Aggregate fuel"): (True, True, False, False, 13),
    ("Total Generation", "Total"): (True, False, False, True, 1),
    ("Total emissions", "Total"): (True, False, False, True, 2),
    ("CO2 intensity", "CO2 intensity"): (True, False, False, False, 1),
}

FUELS = sorted(v for (v, s) in _SOURCE_SPEC if s == "Fuel")
RENEWABLE_FUELS = [v for v in FUELS if _SOURCE_SPEC[(v, "Fuel")][1] is True]

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class ParseResult:
    """Parsed file: as-is raw rows, typed staging rows, and rejects."""

    file_sha256: str
    raw_rows: list[dict] = field(default_factory=list)
    staging_rows: list[dict] = field(default_factory=list)
    rejects: list[dict] = field(default_factory=list)
    reporting_states: list[str] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return len(self.raw_rows)


def parse_csv(path, file_sha256: str) -> ParseResult:
    """Parse the CSV into raw (as-is) and staging (typed) rows plus rejects."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    if list(df.columns) != EXPECTED_COLUMNS:
        raise ValueError(
            f"Unexpected CSV schema: got {list(df.columns)}, expected {EXPECTED_COLUMNS}. "
            "Ember may have changed the file layout; refusing to guess."
        )

    result = ParseResult(file_sha256=file_sha256)
    result.reporting_states = sorted(df["State"].str.strip().unique())

    for idx, row in df.iterrows():
        line_no = idx + 2  # 1-based, +1 for the header row
        clean = {c: row[c].strip() for c in RAW_TEXT_COLUMNS}
        raw_row = {
            "row_number": line_no,
            "country": clean["Country"],
            "country_code": clean["Country code"],
            "state": clean["State"],
            "state_code": clean["State code"],
            "state_type": clean["State type"],
            "date": clean["Date"],
            "category": clean["Category"],
            "subcategory": clean["Subcategory"],
            "variable": clean["Variable"],
            "unit": clean["Unit"],
            "value": clean["Value"],
            "yoy_absolute_change": clean["YoY absolute change"],
            "yoy_pct_change": clean["YoY % change"],
            "source_url": "",
            "file_sha256": file_sha256,
        }
        result.raw_rows.append(raw_row)

        reject_ctx = {
            "row_number": line_no,
            "state": clean["State"],
            "date": clean["Date"],
            "category": clean["Category"],
            "subcategory": clean["Subcategory"],
            "variable": clean["Variable"],
            "unit": clean["Unit"],
            "value": clean["Value"],
        }

        # --- date ----------------------------------------------------------
        if not _DATE_RE.match(clean["Date"]):
            _reject(result, reject_ctx, "unparseable_date")
            continue
        try:
            d = date.fromisoformat(clean["Date"])
        except ValueError:
            _reject(result, reject_ctx, "unparseable_date")
            continue
        if d.day != 1:
            _reject(result, reject_ctx, "date_not_first_of_month")
            continue

        # --- metric mapping --------------------------------------------------
        metric = METRIC_MAP.get((clean["Category"], clean["Unit"]))
        if metric is None:
            _reject(result, reject_ctx, "unknown_metric")
            continue

        # --- value -----------------------------------------------------------
        raw_value = clean["Value"]
        if raw_value == "":
            value = None  # publisher blank: kept as NULL, not rejected
        else:
            try:
                value = float(raw_value)
            except ValueError:
                _reject(result, reject_ctx, "unparseable_value")
                continue

        result.staging_rows.append(
            {
                "reporting_state": clean["State"],
                "state_code": clean["State code"],
                "state_type": clean["State type"],
                "month_date": d,
                "category": clean["Category"],
                "subcategory": clean["Subcategory"],
                "variable": clean["Variable"],
                "unit": clean["Unit"],
                "metric": metric,
                "value": value,
                "file_sha256": file_sha256,
            }
        )

    return result


def _reject(result: ParseResult, ctx: dict, reason: str) -> None:
    result.rejects.append({**ctx, "reason": reason, "file_sha256": result.file_sha256})

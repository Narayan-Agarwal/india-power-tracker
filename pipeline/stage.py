"""Staging layer: typed rows, rejects, and the state mapping table.

State mapping (D6 in DECISIONS.md): Ember's methodology states that Ladakh and
Jammu & Kashmir were reported as one state until July 2021, and that Dadra &
Nagar Haveli and Daman & Diu are one entity throughout. The mapping table
maps every (reporting_state, month) to a canonical entity with validity
windows; anything outside a window is rejected rather than guessed.
"""
from __future__ import annotations

from datetime import date

import psycopg

from .parse import _SOURCE_SPEC

STAGING_COLUMNS = [
    "reporting_state", "state_code", "state_type", "month_date", "category",
    "subcategory", "variable", "unit", "metric", "value", "file_sha256",
]

REJECT_COLUMNS = [
    "file_sha256", "row_number", "state", "date", "category", "subcategory",
    "variable", "unit", "value", "reason",
]

# J&K and Ladakh were reported together until July 2021 (Ember methodology
# PDF, "Notes"). J&K keeps one continuous canonical entity; the mapping table
# documents the break with validity windows. Ladakh only exists from 2021-07.
_JK_SPLIT = date(2021, 7, 1)
_MAPPING_NOTES = {
    "Jammu and Kashmir": [
        (date(1900, 1, 1), date(2021, 6, 30),
         "Includes Ladakh: Ember reports J&K and Ladakh as one state until "
         "July 2021 (subnational methodology PDF, Notes)."),
        (_JK_SPLIT, None,
         "Excludes Ladakh, which is reported separately from 2021-07."),
    ],
    "Ladakh": [
        (_JK_SPLIT, None,
         "Reported separately by Ember from July 2021 (methodology PDF)."),
    ],
    "India Total": [
        (date(1900, 1, 1), None,
         "National aggregate row published by Ember; equals the sum of "
         "states/UTs (verified against the source file)."),
    ],
    "Others": [
        (date(1900, 1, 1), None,
         "Residual bucket for unattributed generation; Ember stopped "
         "reporting it after August 2022."),
    ],
}


def seed_state_mapping(conn: psycopg.Connection, reporting_states: list[str]) -> int:
    """Populate staging.state_mapping from the file's own state labels.

    Canonical names are identical to reporting names (identity mapping); the
    J&K/Ladakh documentation lives in the validity windows and notes.
    """
    rows: list[tuple] = []
    for state in reporting_states:
        windows = _MAPPING_NOTES.get(state, [(date(1900, 1, 1), None, None)])
        for valid_from, valid_to, note in windows:
            rows.append((state, valid_from, valid_to, state, state, note))
    with conn.cursor() as cur:
        cur.execute("TRUNCATE staging.state_mapping")
        cur.executemany(
            "INSERT INTO staging.state_mapping "
            "(reporting_state, valid_from, valid_to, canonical_state, display_name, note) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            rows,
        )
    return len(rows)


def load_staging(
    conn: psycopg.Connection,
    staging_rows: list[dict],
    rejects: list[dict],
) -> tuple[int, int]:
    """Replace staging rows and rejects for the current snapshot."""
    with conn.cursor() as cur:
        cur.execute("TRUNCATE staging.power_monthly")
        cur.execute("TRUNCATE pipeline.rejects")
        if staging_rows:
            with cur.copy(
                "COPY staging.power_monthly "
                f"({', '.join(STAGING_COLUMNS)}) FROM STDIN"
            ) as copy:
                for r in staging_rows:
                    copy.write_row([
                        r["reporting_state"], r["state_code"], r["state_type"],
                        r["month_date"], r["category"], r["subcategory"],
                        r["variable"], r["unit"], r["metric"], r["value"],
                        r["file_sha256"],
                    ])
        if rejects:
            cur.executemany(
                "INSERT INTO pipeline.rejects "
                f"({', '.join(REJECT_COLUMNS)}) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [[r[c] for c in REJECT_COLUMNS] for r in rejects],
            )
    return len(staging_rows), len(rejects)


def source_spec() -> dict[tuple[str, str], tuple[bool, bool, bool, bool, int]]:
    """The (Variable, Subcategory) -> flags spec, straight from parse.py."""
    return _SOURCE_SPEC

"""Raw layer: provenance bookkeeping and as-is row loading.

The CSV is a full snapshot. When the file hash is unchanged the load is
skipped; when it changed, all raw rows are replaced (in one transaction).
"""
from __future__ import annotations

import psycopg

RAW_COLUMNS = [
    "row_number", "country", "country_code", "state", "state_code",
    "state_type", "date", "category", "subcategory", "variable", "unit",
    "value", "yoy_absolute_change", "yoy_pct_change",
    "source_url", "file_sha256",
]


def latest_hash(conn: psycopg.Connection) -> str | None:
    """Most recently loaded file hash, or None if the raw layer is empty."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT file_sha256 FROM raw.file_loads "
            "WHERE action IN ('loaded', 'replaced') ORDER BY load_id DESC LIMIT 1"
        )
        row = cur.fetchone()
    return row[0] if row else None


def previous_newest_month(conn: psycopg.Connection) -> str | None:
    """Newest month seen in the previously loaded raw data (ISO date)."""
    with conn.cursor() as cur:
        cur.execute("SELECT max(date) FROM raw.ember_india_monthly")
        row = cur.fetchone()
    return row[0] if row and row[0] else None


def load_raw(
    conn: psycopg.Connection,
    raw_rows: list[dict],
    source_url: str,
    file_sha256: str,
    file_bytes: int,
    downloaded_at,
    action: str,
) -> int:
    """Replace all raw rows with the new snapshot and record the load.

    `action` is 'loaded' (first load) or 'replaced' (hash changed).
    Runs inside the caller's transaction.
    """
    with conn.cursor() as cur:
        cur.execute("TRUNCATE raw.ember_india_monthly")
        if raw_rows:
            with cur.copy(
                "COPY raw.ember_india_monthly "
                f"({', '.join(RAW_COLUMNS)}) FROM STDIN"
            ) as copy:
                for r in raw_rows:
                    copy.write_row(
                        [r["row_number"], r["country"], r["country_code"],
                         r["state"], r["state_code"], r["state_type"],
                         r["date"], r["category"], r["subcategory"],
                         r["variable"], r["unit"], r["value"],
                         r["yoy_absolute_change"], r["yoy_pct_change"],
                         source_url, r["file_sha256"]]
                    )
        cur.execute(
            "INSERT INTO raw.file_loads "
            "(source_url, file_sha256, file_bytes, row_count, downloaded_at, action) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (source_url, file_sha256, file_bytes, len(raw_rows), downloaded_at, action),
        )
    return len(raw_rows)


def record_skip(
    conn: psycopg.Connection,
    source_url: str,
    file_sha256: str,
    file_bytes: int,
    row_count: int,
    downloaded_at,
) -> None:
    """Record a refresh attempt where the hash was unchanged (no data change)."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO raw.file_loads "
            "(source_url, file_sha256, file_bytes, row_count, downloaded_at, action) "
            "VALUES (%s, %s, %s, %s, %s, 'skipped_unchanged')",
            (source_url, file_sha256, file_bytes, row_count, downloaded_at),
        )

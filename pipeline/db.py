"""Database connection helpers.

Connection strings are never logged, printed, or included in exceptions that
we surface; psycopg errors may echo the host, so we redact the dsn anywhere a
message could be shown.
"""
from __future__ import annotations

import time
from contextlib import contextmanager

import psycopg

from .config import database_url, target_kwargs


def _redact(obj) -> str:
    try:
        return psycopg.conninfo.make_conninfo(obj or "").split("?")[0].rsplit("@", 1)[-1]
    except Exception:
        return "<redacted>"


def connect(target: str, attempts: int = 3, retry_delay: float = 5.0) -> psycopg.Connection:
    """Open a connection with per-target settings and retries.

    Neon's compute auto-suspends when idle, so the first connection can take a
    while; we retry rather than fail the run.
    """
    url = database_url(target)
    kwargs = target_kwargs(target)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return psycopg.connect(url, **kwargs)
        except psycopg.OperationalError as exc:  # includes timeouts
            last_error = exc
            if attempt < attempts:
                time.sleep(retry_delay * attempt)
    raise RuntimeError(
        f"Could not connect to target '{target}' after {attempts} attempts "
        f"(host={_redact(url)}): {type(last_error).__name__}"
    ) from last_error


@contextmanager
def connection(target: str, attempts: int = 3):
    """Context-managed connection."""
    conn = connect(target, attempts=attempts)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def execute_file(conn: psycopg.Connection, sql_path) -> None:
    """Execute a .sql file (multiple statements allowed) in one transaction."""
    sql = sql_path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)

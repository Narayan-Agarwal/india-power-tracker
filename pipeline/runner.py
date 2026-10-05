"""Pipeline orchestration: backfill and refresh, with run_log bookkeeping.

backfill: download, and always rebuild raw -> staging -> marts in one
          transaction, then run the quality gate.
refresh:  download; if the file hash is unchanged, skip the rebuild and only
          re-run the quality gate; if it changed, behave like backfill.

Both paths record exactly one pipeline.run_log row.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from . import db, extract, marts as marts_mod, parse, rawload, stage
from .config import DATASET_URL, RAW_DOWNLOAD_PATH, REPO_ROOT
from .db import execute_file
from .quality import run_all_checks

SQL_DIR = REPO_ROOT / "sql"


class QualityGateError(RuntimeError):
    """Raised when a hard quality check fails; makes the run exit non-zero."""


def run_pipeline(command: str, target: str) -> int:
    run_id = uuid.uuid4()
    started = datetime.now(timezone.utc)
    conn = db.connect(target, attempts=4)
    conn.autocommit = True  # explicit transaction blocks below
    log_values: dict = {
        "run_id": run_id, "command": command, "target": target,
        "started_at": started, "status": "failed",
    }
    try:
        execute_file(conn, SQL_DIR / "10_schema.sql")

        # 1. Download (always: refresh must detect a new file).
        path, sha, size = extract.download_file(DATASET_URL, RAW_DOWNLOAD_PATH)
        log_values["file_sha256"] = sha
        downloaded_at = datetime.now(timezone.utc)

        prev_hash = rawload.latest_hash(conn)
        unchanged = (sha == prev_hash)
        force = (command == "backfill")
        rebuild = force or not unchanged

        if not rebuild:
            rawload.record_skip(
                conn, DATASET_URL, sha, size, _count(conn, "raw.ember_india_monthly"),
                downloaded_at,
            )
            log_values.update(
                raw_rows_in=_count(conn, "raw.ember_india_monthly"),
                staging_rows_out=_count(conn, "staging.power_monthly"),
                fact_rows_out=_count(conn, "marts.fact_power_monthly"),
                rejects=_count(conn, "pipeline.rejects"),
            )
            message = "file hash unchanged; raw/staging/marts left untouched"
        else:
            # 2. Parse (in memory): raw rows as-is, typed rows, rejects.
            parsed = parse.parse_csv(path, sha)
            action = "loaded" if prev_hash is None else "replaced"

            # 3. Rebuild everything in ONE transaction: raw -> staging -> marts.
            with conn.transaction():
                rawload.load_raw(
                    conn, parsed.raw_rows, DATASET_URL, sha, size, downloaded_at, action
                )
                stage.seed_state_mapping(conn, parsed.reporting_states)
                st_count, rej_count = stage.load_staging(
                    conn, parsed.staging_rows, parsed.rejects
                )
                counts = marts_mod.build_marts(conn)
            log_values.update(
                raw_rows_in=len(parsed.raw_rows),
                staging_rows_out=st_count,
                fact_rows_out=counts["marts.fact_power_monthly"],
                rejects=rej_count,
            )
            message = (
                f"{action} raw snapshot {sha[:12]} ({size:,} bytes); "
                f"staging={st_count:,}; fact={counts['marts.fact_power_monthly']:,}; "
                f"rejects={rej_count}"
            )

        # 4. Quality gate + reconciliation (read-only).
        checks = run_all_checks(
            conn,
            raw_rows=log_values["raw_rows_in"],
            staging_rows=log_values["staging_rows_out"],
            rejects=log_values["rejects"],
        )
        log_values["checks"] = [c.as_dict() for c in checks]
        failed = [c for c in checks if not c.passed and c.severity == "hard"]
        if failed:
            raise QualityGateError(
                "quality gate failed: "
                + "; ".join(f"{c.name} ({c.detail})" for c in failed)
            )

        log_values["status"] = "success"
        log_values["message"] = message
        _write_log(conn, log_values, finished=datetime.now(timezone.utc))
        print(f"[ok] {command} on {target}: {message}")
        for c in checks:
            mark = "PASS" if c.passed else "FAIL"
            print(f"     [{mark}] {c.severity:4} {c.name}: {c.detail}")
        return 0

    except Exception as exc:
        log_values["message"] = f"{type(exc).__name__}: {exc}"
        try:
            _write_log(conn, log_values, finished=datetime.now(timezone.utc))
        except Exception:
            pass  # never mask the original error with a logging failure
        print(f"[FAIL] {command} on {target}: {log_values['message']}")
        return 1
    finally:
        conn.close()


def _count(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        return cur.fetchone()[0]


def _write_log(conn, values: dict, finished: datetime) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO pipeline.run_log
                (run_id, command, target, started_at, finished_at, status,
                 file_sha256, raw_rows_in, staging_rows_out, fact_rows_out,
                 rejects, message, details)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                values["run_id"], values["command"], values["target"],
                values["started_at"], finished, values["status"],
                values.get("file_sha256"), values.get("raw_rows_in"),
                values.get("staging_rows_out"), values.get("fact_rows_out"),
                values.get("rejects"), values.get("message"),
                json.dumps(values.get("checks")) if values.get("checks") else None,
            ),
        )

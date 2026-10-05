"""Connectivity check for both configured targets.

Prints status only — never connection strings.
"""
from __future__ import annotations

import sys

from . import db


def _one(target: str) -> bool:
    try:
        with db.connection(target, attempts=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                (version,) = cur.fetchone()
            nice = version.split(",")[0]
            print(f"[ok] {target}: connected ({nice})")
        return True
    except Exception as exc:
        print(f"[FAIL] {target}: {type(exc).__name__}: {exc}")
        return False


def run_check() -> int:
    ok_local = _one("local")
    ok_neon = _one("neon")
    if not (ok_local and ok_neon):
        print("One or more databases are unreachable. See the messages above.")
        return 1
    print("Both databases are reachable.")
    return 0


if __name__ == "__main__":
    sys.exit(run_check())

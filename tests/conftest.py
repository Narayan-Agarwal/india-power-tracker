"""Shared fixtures for the test suite.

Strategy (DECISIONS.md D12): tests exercise the REAL pipeline against the
LOCAL database only — never Neon. Parsing edge cases are pure Python.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

TEST_TARGET = os.environ.get("IPT_TEST_TARGET", "local")

# The app prefers NEON_DATABASE_URL over DATABASE_URL; tests must never hit
# Neon (slow cold starts, and the app must be tested against local marts).
# An empty value falls through to DATABASE_URL in app.core._database_url.
os.environ["NEON_DATABASE_URL"] = ""


@pytest.fixture(scope="session")
def db_ready() -> str:
    """Skip DB-dependent tests when the local database is not configured."""
    from pipeline.config import ConfigError, database_url

    if TEST_TARGET != "local":
        pytest.fail("tests must run against the local database (IPT_TEST_TARGET=local)")
    try:
        url = database_url("local")
    except ConfigError as exc:
        pytest.skip(f"local database not configured: {exc}")
    return url


@pytest.fixture(scope="session")
def local_pipeline(db_ready):
    """Ensure the local database has current marts; returns the URL."""
    from pipeline.runner import run_pipeline

    rc = run_pipeline("backfill", "local")
    assert rc == 0, "pipeline backfill failed during test setup"
    return db_ready

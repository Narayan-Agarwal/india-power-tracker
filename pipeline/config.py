"""Configuration: paths, dataset URL, and target database resolution.

Secrets are read from the repo-root .env and are never logged or printed.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

# Load .env from the repo root (idempotent).
load_dotenv(REPO_ROOT / ".env")

# --- Dataset identity -------------------------------------------------------
# Verified against ember-energy.org on 2026-10-05 (see DECISIONS.md D1/D2):
# the download link labelled "India monthly electricity data – long format
# (CSV)" on https://ember-energy.org/data/india-electricity-data
DATASET_NAME = "Ember India electricity data (state-level, monthly)"
DATASET_PAGE_URL = "https://ember-energy.org/data/india-electricity-data"
DATASET_LICENCE = "CC BY 4.0 (as stated by Ember: 'All content is released under a Creative Commons Attribution Licence (CC-BY-4.0).')"
DATASET_URL = os.environ.get(
    "EMBER_DATASET_URL",
    "https://files.ember-energy.org/public-downloads/india_monthly_full_release_long_format.csv",
)

# --- Paths ------------------------------------------------------------------
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
RAW_DOWNLOAD_PATH = RAW_DIR / "ember_india_electricity.csv"

# --- Database targets -------------------------------------------------------
_TARGET_ENV_VAR = {"local": "DATABASE_URL", "neon": "NEON_DATABASE_URL"}


class ConfigError(RuntimeError):
    pass


def database_url(target: str) -> str:
    """Return the connection URL for a target without ever logging it."""
    try:
        env_var = _TARGET_ENV_VAR[target]
    except KeyError:
        raise ConfigError(f"Unknown target {target!r}; use 'local' or 'neon'") from None
    url = os.environ.get(env_var, "").strip()
    if not url:
        raise ConfigError(
            f"{env_var} is not set; expected it in the repo-root .env file"
        )
    return url


def target_kwargs(target: str) -> dict:
    """psycopg connect kwargs tuned per target."""
    kwargs: dict = {
        "application_name": "india-power-tracker",
        "connect_timeout": 15,
    }
    if target == "neon":
        # Neon requires TLS and its compute may be suspended; allow a slow
        # first connection (handled with retries in pipeline.db).
        kwargs["sslmode"] = "require"
    return kwargs


# --- Pipeline knobs ---------------------------------------------------------
# Tolerance for "sources sum to totals" checks, as a fraction of the total.
SUM_TOLERANCE_FRACTION = 0.005  # 0.5%
# (Freshness is a regression guard plus a reported lag — see DECISIONS.md D9.)

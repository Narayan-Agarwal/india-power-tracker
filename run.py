"""Entry point: python run.py backfill|refresh|check|test|app --target local|neon

- backfill: create schemas/tables, download + load raw, rebuild staging and
  marts from scratch, run quality checks. Safe to re-run (idempotent).
- refresh:  same as backfill, but the raw load is skipped when the source file
  hash is unchanged; marts are rebuilt only if new raw data was loaded.
- check:    verify both configured databases are reachable (no data changes).
- test:     run the pytest suite (default target local).
- app:      launch the Streamlit app pointed at the chosen target.
"""
from __future__ import annotations

import argparse
import os
import sys

from pipeline.config import REPO_ROOT


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("backfill", "refresh", "check", "test", "app"):
        p = sub.add_parser(name)
        p.add_argument("--target", choices=("local", "neon"), default="local")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "check":
        from pipeline.dbcheck import run_check

        return run_check()

    if args.command == "test":
        import pytest

        os.environ.setdefault("IPT_TEST_TARGET", args.target)
        raise SystemExit(pytest.main(["-q", str(REPO_ROOT / "tests")]))

    if args.command == "app":
        from streamlit.web import cli as stcli

        os.environ["IPT_APP_TARGET"] = args.target
        app_path = REPO_ROOT / "app" / "streamlit_app.py"
        sys.exit(
            stcli.main(
                ["run", str(app_path), "--server.headless", "true", "--browser.gatherUsageStats", "false"]
            )
        )

    from pipeline.runner import run_pipeline

    return run_pipeline(args.command, args.target)


if __name__ == "__main__":
    raise SystemExit(main())

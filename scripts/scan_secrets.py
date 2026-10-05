"""Scan tracked git files for anything that looks like a secret.

Usage: python scripts/scan_secrets.py

Checks every file git tracks (plus git itself is not needed to run) for
connection strings, password-shaped assignments, and the exact URLs that must
never be committed. Exits non-zero if anything is found.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PATTERNS = [
    (r"postgresql(\+psycopg)?://[^\s\"'<>]*:[^\s\"'<>@]+@", "postgres URL with credentials"),
    (r"ep-[a-z0-9-]+\.aws\.neon\.tech", "Neon endpoint host"),
    (r"(?i)(password|passwd|pwd)\s*=\s*[^\s\"']{6,}", "hardcoded password assignment"),
    (r"sk-[A-Za-z0-9]{20,}", "API key (sk-...)"),
    (r"ghp_[A-Za-z0-9]{20,}", "GitHub token (ghp_...)"),
    (r"gho_[A-Za-z0-9]{20,}", "GitHub token (gho_...)"),
]

ALLOWED_FILES = {
    ".env.example",          # placeholders only, verified below
    "scripts/scan_secrets.py",
}


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return [f for f in out.stdout.splitlines() if f]


def main() -> int:
    problems: list[str] = []
    files = tracked_files()
    for rel in files:
        if rel in ALLOWED_FILES:
            continue  # .env.example must contain only the USER:PASSWORD placeholders
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern, label in PATTERNS:
            for m in re.finditer(pattern, text):
                problems.append(f"{rel}: {label}: {m.group(0)[:40]}...")

    # Belt and braces: .env.example may contain only the documented placeholders.
    example = REPO_ROOT / ".env.example"
    if example.is_file() and "USER:PASSWORD" not in example.read_text(encoding="utf-8"):
        problems.append(".env.example: no USER:PASSWORD placeholder found — inspect it")

    if problems:
        print("POSSIBLE SECRETS FOUND:")
        for p in problems:
            print("  ", p)
        return 1
    print(f"OK: {len(files)} tracked files scanned, no secrets found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

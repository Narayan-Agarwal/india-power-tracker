"""Download the source CSV with resume + retries, and hash it.

files.ember-energy.org throttles connections and sometimes resets them
mid-download, so the downloader resumes from the byte offset already on disk.
"""
from __future__ import annotations

import hashlib
import time
import urllib.error
import urllib.request
from pathlib import Path

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) india-power-tracker-pipeline"
)


class DownloadError(RuntimeError):
    pass


def download_file(
    url: str,
    dest: Path,
    attempts: int = 8,
    timeout: float = 120.0,
    max_total_seconds: float = 3000.0,
) -> tuple[Path, str, int]:
    """Download `url` to `dest` (resuming if a partial file exists).

    Returns (path, sha256_hex, size_bytes).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    last_error: Exception | None = None

    if dest.exists():
        remote_size = _remote_size(url, timeout)
        if remote_size is not None and dest.stat().st_size == remote_size:
            return _finish(dest, started)  # already complete; skip the download

    for attempt in range(1, attempts + 1):
        existing = dest.stat().st_size if dest.exists() else 0
        headers = {"User-Agent": USER_AGENT}
        if existing:
            headers["Range"] = f"bytes={existing}-"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if existing and resp.status != 206:
                    # Server ignored the Range header; restart from scratch.
                    existing = 0
                mode = "ab" if existing else "wb"
                with open(dest, mode) as f:
                    while chunk := resp.read(1 << 16):
                        f.write(chunk)
            return _finish(dest, started)
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and existing:
                # Range not satisfiable: the file on disk is already complete.
                return _finish(dest, started)
            last_error = exc
            if time.monotonic() - started > max_total_seconds:
                break
            time.sleep(min(3 * attempt, 15))
        except Exception as exc:  # noqa: BLE001 — any network error is retryable
            last_error = exc
            if time.monotonic() - started > max_total_seconds:
                break
            time.sleep(min(3 * attempt, 15))
    raise DownloadError(
        f"Failed to download {url} after {attempts} attempts: {last_error}"
    ) from last_error


def _remote_size(url: str, timeout: float) -> int | None:
    """Content-Length from a HEAD request, or None if unavailable."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            length = resp.headers.get("Content-Length")
            return int(length) if length else None
    except Exception:  # noqa: BLE001 — fall back to the Range download path
        return None


def _finish(dest: Path, started: float) -> tuple[Path, str, int]:
    sha = hashlib.sha256()
    size = 0
    with open(dest, "rb") as f:
        while chunk := f.read(1 << 20):
            sha.update(chunk)
            size += len(chunk)
    return dest, sha.hexdigest(), size

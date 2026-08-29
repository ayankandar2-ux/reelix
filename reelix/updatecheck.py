"""Lightweight self-update check against GitHub.

Reads the local VERSION file shipped inside the package, compares it to
the same file's current content on the main branch, and reports a
mismatch. This never installs anything itself -- it just tells the user
a `git pull && bash install.sh` would get them something newer.
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

REPO_RAW_VERSION_URL = (
    "https://raw.githubusercontent.com/ayankandar2-ux/reelix/main/reelix/VERSION"
)
_VERSION_PATH = Path(__file__).resolve().parent / "VERSION"


def local_version() -> str:
    try:
        return _VERSION_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def check_for_update(timeout: float = 2.5) -> str | None:
    """Return the remote version string if it differs from the local one,
    otherwise None. Never raises -- a failed or slow network check just
    means no notice is shown; it must never block or crash startup."""
    try:
        with urllib.request.urlopen(REPO_RAW_VERSION_URL, timeout=timeout) as resp:
            remote = resp.read().decode("utf-8", errors="replace").strip()
    except Exception:
        return None
    local = local_version()
    if remote and remote != local:
        return remote
    return None

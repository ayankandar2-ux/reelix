"""Small stateless helper functions shared across Reelix modules."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

ILLEGAL_CHARS = r'[\/:*?"<>|\x00-\x1f]'


def which(cmd: str) -> str | None:
    """Return the resolved path to `cmd` if it exists on PATH, else None."""
    return shutil.which(cmd)


def human_size(num_bytes: float | None) -> str:
    """Format a byte count as a short human-readable string (MiB/GiB style)."""
    if num_bytes is None:
        return "unknown"
    size = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TiB"


def human_speed(bytes_per_sec: float | None) -> str:
    if not bytes_per_sec:
        return "-"
    return f"{human_size(bytes_per_sec)}/s"


def format_eta(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    return format_eta(seconds)


def sanitize_filename(name: str, max_len: int = 150) -> str:
    """Strip characters that are illegal on Android/Linux filesystems."""
    if not name:
        name = "video"
    cleaned = re.sub(ILLEGAL_CHARS, "_", name)
    cleaned = cleaned.strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = "video"
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip()
    return cleaned


def unique_path(directory: Path, filename: str) -> Path:
    """Return a path in `directory` for `filename`, avoiding collisions by
    appending ' (1)', ' (2)', etc. before the extension."""
    directory = Path(directory)
    base = Path(filename)
    stem, suffix = base.stem, base.suffix
    candidate = directory / filename
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem} ({counter}){suffix}"
        counter += 1
    return candidate


def looks_like_url(text: str) -> bool:
    text = text.strip()
    return bool(re.match(r"^https?://[^\s]+$", text, re.IGNORECASE))

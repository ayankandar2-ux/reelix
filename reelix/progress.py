"""Parse yt-dlp's --newline progress output into structured events.

We never show this raw text to the user -- app.py/ui.py render it into the
clean DOWNLOADING panel instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_UNITS = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}

_DOWNLOAD_RE = re.compile(
    r"\[download\]\s+(?P<percent>[\d.]+)%\s+of\s+~?(?P<total>[\d.]+)(?P<total_unit>[KMGT]?i?B)"
    r"(?:\s+at\s+(?P<speed>[\d.]+)(?P<speed_unit>[KMGT]?i?B)/s)?"
    r"(?:\s+ETA\s+(?P<eta>[\d:]+))?"
)
_DEST_RE = re.compile(r"\[download\]\s+Destination:\s+(?P<dest>.+)")
_MERGE_RE = re.compile(r"\[Merger\]")
_ALREADY_RE = re.compile(r"has already been downloaded")


def _to_bytes(value: str, unit: str) -> float:
    return float(value) * _UNITS.get(unit, 1)


def _eta_to_seconds(text: str | None) -> float | None:
    if not text:
        return None
    parts = [int(p) for p in text.split(":")]
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds


@dataclass
class ProgressEvent:
    kind: str  # "progress" | "merging" | "destination" | "other"
    percent: float | None = None
    downloaded_bytes: float | None = None
    total_bytes: float | None = None
    speed_bytes: float | None = None
    eta_seconds: float | None = None
    destination: str | None = None


def parse_line(line: str) -> ProgressEvent | None:
    line = line.strip()
    if not line:
        return None

    match = _DOWNLOAD_RE.search(line)
    if match:
        percent = float(match.group("percent"))
        total = _to_bytes(match.group("total"), match.group("total_unit"))
        speed = None
        if match.group("speed"):
            speed = _to_bytes(match.group("speed"), match.group("speed_unit"))
        eta = _eta_to_seconds(match.group("eta"))
        return ProgressEvent(
            kind="progress",
            percent=percent,
            downloaded_bytes=total * percent / 100.0,
            total_bytes=total,
            speed_bytes=speed,
            eta_seconds=eta,
        )

    if _MERGE_RE.search(line):
        return ProgressEvent(kind="merging")

    dest_match = _DEST_RE.search(line)
    if dest_match:
        return ProgressEvent(kind="destination", destination=dest_match.group("dest"))

    if _ALREADY_RE.search(line):
        return ProgressEvent(kind="progress", percent=100.0)

    return None

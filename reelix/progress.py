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
# aria2c's OWN status line (used whenever --downloader aria2c is active --
# yt-dlp hands the download off entirely, so its native "[download] xx%"
# line above never appears; this is the line that actually streams by):
#   [#2089b0 SIZE:12.3MiB/45.6MiB(27%) CN:8 DL:2.1MiB ETA:15s]
# "SIZE:" prefix and the trailing DL:/ETA: fields are optional across
# aria2c versions, so each piece is matched independently rather than as
# one rigid template.
_ARIA2_RE = re.compile(
    r"\[#\S+\s+(?:SIZE:)?(?P<downloaded>[\d.]+)(?P<downloaded_unit>[KMGT]?i?B)"
    r"/(?P<total>[\d.]+)(?P<total_unit>[KMGT]?i?B)\((?P<percent>[\d.]+)%\)"
    r"(?:[^\]]*?\bDL:(?P<speed>[\d.]+)(?P<speed_unit>[KMGT]?i?B))?"
    r"(?:[^\]]*?\bETA:(?P<eta>[a-zA-Z0-9]+))?"
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


def _aria2_eta_to_seconds(text: str | None) -> float | None:
    """aria2c writes ETA as '15s', '2m30s', '1h5m3s' -- not colon-separated
    like yt-dlp's own downloader."""
    if not text:
        return None
    units = {"h": 3600, "m": 60, "s": 1}
    total = 0
    matched = False
    for value, unit in re.findall(r"(\d+)([hms])", text):
        total += int(value) * units[unit]
        matched = True
    return float(total) if matched else None


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

    aria_match = _ARIA2_RE.search(line)
    if aria_match:
        downloaded = _to_bytes(aria_match.group("downloaded"), aria_match.group("downloaded_unit"))
        total = _to_bytes(aria_match.group("total"), aria_match.group("total_unit"))
        percent = float(aria_match.group("percent"))
        speed = None
        if aria_match.group("speed"):
            speed = _to_bytes(aria_match.group("speed"), aria_match.group("speed_unit"))
        eta = _aria2_eta_to_seconds(aria_match.group("eta"))
        return ProgressEvent(
            kind="progress",
            percent=percent,
            downloaded_bytes=downloaded,
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

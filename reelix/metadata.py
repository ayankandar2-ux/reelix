"""Fetch and lightly normalize video metadata using yt-dlp's JSON output."""
from __future__ import annotations

import json
import subprocess

from .errors import ReelixError, map_ytdlp_error

# Informational-only mapping from yt-dlp extractor keys to a friendly name.
_SOURCE_NAMES = {
    "youtube": "YouTube",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "tiktok": "TikTok",
    "twitter": "Twitter / X",
    "reddit": "Reddit",
    "vimeo": "Vimeo",
    "dailymotion": "Dailymotion",
    "twitch": "Twitch",
}


def detect_source(info: dict) -> str:
    extractor = (info.get("extractor_key") or info.get("extractor") or "").lower()
    for key, name in _SOURCE_NAMES.items():
        if key in extractor:
            return name
    return info.get("extractor_key") or "Unknown"


def fetch_info(url: str, debug: bool = False, timeout: int = 60) -> dict:
    """Run `yt-dlp -j` on the URL and return the parsed metadata dict.

    Raises ReelixError with a friendly message on any failure.
    """
    cmd = [
        "yt-dlp",
        "-j",
        "--no-warnings",
        "--no-playlist",
        url,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise ReelixError(
            "yt-dlp missing",
            "yt-dlp isn't installed or isn't on your PATH.",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ReelixError(
            "Timed out",
            "Fetching video information took too long. Check your "
            "connection and try again.",
        ) from exc

    if result.returncode != 0 or not result.stdout.strip():
        raise map_ytdlp_error(result.stderr or result.stdout)

    # yt-dlp -j prints one JSON object per line (per video). We only take
    # the first, since --no-playlist limits us to a single video.
    first_line = result.stdout.strip().splitlines()[0]
    try:
        info = json.loads(first_line)
    except json.JSONDecodeError as exc:
        raise ReelixError(
            "Couldn't read video info",
            "yt-dlp returned data Reelix couldn't understand.",
            raw=result.stdout,
        ) from exc

    info["_source_name"] = detect_source(info)
    return info

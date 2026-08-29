"""Download history -- a small local JSON log of past downloads.

Lives at ~/.config/reelix/history.json, capped at MAX_ENTRIES most-recent
entries so it never grows unbounded.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

HISTORY_DIR = Path.home() / ".config" / "reelix"
HISTORY_PATH = HISTORY_DIR / "history.json"
MAX_ENTRIES = 50


def load_history() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return []


def add_entry(entry: dict) -> None:
    entries = load_history()
    entries.insert(0, entry)
    entries = entries[:MAX_ENTRIES]
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2)
    except OSError:
        pass


def entry_now(url: str, title: str, quality: str, container: str, size: str, dest: str) -> dict:
    return {
        "url": url,
        "title": title,
        "quality": quality,
        "container": container,
        "size": size,
        "dest": dest,
        "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

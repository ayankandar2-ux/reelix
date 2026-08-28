"""Configuration handling for Reelix.

Config lives at ~/.config/reelix/config.json and never needs to be touched by
hand for normal operation -- sensible defaults are written the first time
Reelix runs.
"""
from __future__ import annotations

import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "reelix"
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULT_DOWNLOAD_DIR = "/storage/emulated/0/Movies/Reelix"

DEFAULTS = {
    "download_dir": DEFAULT_DOWNLOAD_DIR,
    "aria2_connections": 8,
    "aria2_split": 8,
    "aria2_min_split_size": "1M",
    "default_container": "mp4",
    "preferred_qualities": [360, 480, 720],
    "color_enabled": True,
    "debug": False,
}


def load_config() -> dict:
    """Load config, creating it with defaults if it doesn't exist yet."""
    if not CONFIG_PATH.exists():
        return save_config(DEFAULTS.copy())

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return save_config(DEFAULTS.copy())

    # Backfill any keys that a future version might have added.
    changed = False
    for key, value in DEFAULTS.items():
        if key not in data:
            data[key] = value
            changed = True
    if changed:
        save_config(data)
    return data


def save_config(data: dict) -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return data

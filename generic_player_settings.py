"""Bounded session persistence with tolerant loads and all-or-nothing saves.

Every save validates a fresh queue snapshot. Do not cache normalized entries
by list identity: queue entries are mutable and local path resolution can
change independently of the queue. atomic_write_text preserves the previous
session when validation, encoding, or the byte limit prevents a new write.
"""
from __future__ import annotations

import json
import math
import os
from itertools import chain
from pathlib import Path

from generic_player_library import atomic_write_text, normalize_playlist, read_bounded_text

MAX_SETTINGS_BYTES = 12 * 1024 * 1024


def settings_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "generic_player" / "session.json"


def _number(value, default, lower, upper, integer=False):
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or (isinstance(value, float) and not math.isfinite(value))):
        return default
    value = max(lower, min(upper, value))
    return int(value) if integer else float(value)


def validate_settings(data: dict, *, strict_playlist=False) -> dict:
    """Return independent normalized entries and bounded scalar preferences.

    Loading can recover scalar preferences from a damaged queue. Saving uses
    strict_playlist so a bad in-memory queue cannot erase a valid saved one.
    """
    if not isinstance(data, dict):
        if strict_playlist:
            raise ValueError("Session settings must be an object.")
        data = {}
    result = dict(data)
    try:
        result["playlist"] = normalize_playlist(data.get("playlist", []))
    except (TypeError, ValueError, OSError):
        if strict_playlist:
            raise
        result["playlist"] = []
    result["current_song_index"] = _number(data.get("current_song_index"), -1, -1,
                                             len(result["playlist"]) - 1, True)
    result["position_ms"] = _number(data.get("position_ms"), 0, 0, 2 ** 31 - 1, True)
    result["volume"] = _number(data.get("volume"), 70, 0, 100, True)
    result["muted"] = data.get("muted") is True
    result["shuffle"] = data.get("shuffle") is True
    result["repeat"] = data.get("repeat") if isinstance(data.get("repeat"), str) and data.get("repeat") in {"off", "all", "one"} else "off"
    result["playback_rate"] = _number(data.get("playback_rate"), 1.0, 0.25, 3.0)
    result["visualizer"] = data.get("visualizer") if isinstance(data.get("visualizer"), str) else "Spectrum"
    result["geometry"] = data.get("geometry") if isinstance(data.get("geometry"), str) else ""
    # Older sessions had no notification preference. Detect available services
    # for those sessions while respecting an explicitly saved Off selection.
    mode = data.get("osd_mode", "auto")
    result["osd_mode"] = mode if isinstance(mode, str) and mode in {"auto", "off", "desktop", "wayland", "custom"} else "off"
    return result


def load_settings() -> dict:
    try:
        path = settings_path()
        return validate_settings(json.loads(read_bounded_text(path, MAX_SETTINGS_BYTES)))
    except (OSError, ValueError, TypeError, RecursionError):
        return validate_settings({})


def save_settings(data: dict) -> bool:
    """Write one validated session, returning False without replacing on error."""
    try:
        state = validate_settings(data, strict_playlist=True)
        chunks = chain(json.JSONEncoder(ensure_ascii=False, indent=2).iterencode(state), ("\n",))
        atomic_write_text(settings_path(), chunks, max_bytes=MAX_SETTINGS_BYTES)
        return True
    except (OSError, ValueError, TypeError, RecursionError):
        return False

"""On-disk JSON cache for lyrics lookups, keyed by track."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .. import config
from ..text.metadata import normalized
from .output import log_error


def cache_path(key: str) -> Path:
    return config.CACHE_DIR / f"{key}.json"


def lock_path(key: str) -> Path:
    return config.CACHE_DIR / f"{key}.lock"


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    os.replace(temporary, path)


def read_cache(key: str) -> dict[str, Any] | None:
    path = cache_path(key)
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        log_error(f"Could not read cache {path}: {exc}")
        try:
            path.unlink()
        except OSError:
            pass
        return None


def read_compatible_cache(key: str, track: dict[str, Any]) -> dict[str, Any] | None:
    cached = read_cache(key)
    if cached is not None and cached.get("status") != "not_found":
        return cached

    title = normalized(str(track.get("title", "") or ""))
    artist = normalized(str(track.get("artist", "") or ""))
    album = normalized(str(track.get("album", "") or ""))
    duration = float(track.get("duration", 0.0) or 0.0)
    if not title or not artist or not duration:
        return cached

    compatible: dict[str, Any] | None = None
    for path in config.CACHE_DIR.glob("*.json"):
        if path == cache_path(key):
            continue
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
            record = candidate.get("record") or {}
            record_duration = float(record.get("duration", 0.0) or 0.0)
        except (OSError, TypeError, ValueError):
            continue
        # Records written under an older cache version were made by provider
        # behavior that may not match this track anymore; never resurrect them.
        if candidate.get("cache_version") != config.CACHE_KEY_VERSION:
            continue
        if candidate.get("status") == "ok" and record.get("source") == "apple-cache":
            try:
                path.unlink()
            except OSError:
                pass
            continue
        if (
            candidate.get("status") == "ok"
            and normalized(str(record.get("trackName", "") or "")) == title
            and normalized(str(record.get("artistName", "") or "")) == artist
            and normalized(str(record.get("albumName", "") or "")) == album
            and abs(record_duration - duration) <= 2.0
        ):
            compatible = candidate

    if compatible is not None:
        atomic_write_json(cache_path(key), compatible)
        return compatible

    return cached

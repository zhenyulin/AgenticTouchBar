"""Reading Apple Music's cached lyrics in the detached fetch helper."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

from . import config
from .cache import atomic_write_json, cache_path, lock_path
from .locking import acquire_lock, clear_lock, spawn_helper
from .lrc import parse_lrc
from .output import log_error, trace
from .providers.apple_cache import apple_cache_record


def fetch_lyrics_record(track: dict[str, Any]) -> dict[str, Any] | None:
    return apple_cache_record(track)


def background_fetch(key: str, track: dict[str, Any]) -> None:
    lock = lock_path(key)
    started = time.monotonic()
    try:
        try:
            record = fetch_lyrics_record(track)
            now = time.time()
            if record is None:
                payload = {
                    "status": "not_found",
                    "fetched_at": now,
                    "retry_after": now + config.RETRY_NOT_FOUND_SECONDS,
                }
            elif record.get("instrumental"):
                payload = {
                    "status": "instrumental",
                    "fetched_at": now,
                    "retry_after": now + config.RETRY_NOT_FOUND_SECONDS,
                    "record": record,
                }
            else:
                record = dict(record)
                record["parsedLines"] = parse_lrc(record.get("syncedLyrics") or "")
                payload = {
                    "status": "ok",
                    "fetched_at": now,
                    "record": record,
                }
            atomic_write_json(cache_path(key), payload)
            trace(
                "fetch",
                started,
                str(payload["status"]),
                source=str((record or {}).get("source", "-")),
            )
        except Exception as exc:
            now = time.time()
            log_error(
                f"Lyrics fetch failed for {track.get('artist')} — {track.get('title')}: {exc}"
            )
            atomic_write_json(
                cache_path(key),
                {
                    "status": "cache_error",
                    "message": str(exc),
                    "fetched_at": now,
                    "retry_after": now + config.RETRY_CACHE_ERROR_SECONDS,
                },
            )
            trace("fetch", started, "cache_error", reason=str(exc).split(":")[0][:40])
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def start_background_fetch(key: str, track: dict[str, Any]) -> bool:
    lock = lock_path(key)
    if not acquire_lock(lock, config.LOCK_MAX_AGE_SECONDS):
        return False

    payload = base64.urlsafe_b64encode(
        json.dumps(track, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    try:
        spawn_helper(["--fetch", key, payload])
        return True
    except Exception:
        clear_lock(lock)
        raise


def start_sampler() -> bool:
    """Ask a detached helper for a fresh Apple Music sample.

    The lock keeps one sample in flight at a time, so a run of quick widget
    ticks cannot pile up osascript processes behind an unresponsive Music.
    """
    if not acquire_lock(config.SAMPLER_LOCK_PATH, config.SAMPLER_LOCK_MAX_AGE_SECONDS):
        return False

    try:
        spawn_helper(["--sample"])
        return True
    except Exception:
        clear_lock(config.SAMPLER_LOCK_PATH)
        raise

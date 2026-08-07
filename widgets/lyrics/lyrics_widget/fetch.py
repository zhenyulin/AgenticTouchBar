"""Choosing a lyrics record for a track, and running that lookup in the
detached background-fetch helper process."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

from . import config
from .cache import atomic_write_json, cache_path, lock_path
from .concurrency import set_fetch_deadline
from .locking import acquire_lock, clear_lock, spawn_helper
from .lrc import parse_lrc
from .output import log_error, trace
from .providers.local import local_lyrics_record
from .providers.lrcapi import choose_lrcapi_candidate
from .providers.lrclib import lrclib_record


def fetch_lyrics_record(track: dict[str, Any]) -> dict[str, Any] | None:
    # Provider order: user-maintained local LRC, token-free Chinese sources,
    # then the open LRCLIB database.
    from concurrent.futures import ThreadPoolExecutor

    local = local_lyrics_record(track)
    if local is not None:
        return local

    # LrcAPI's public endpoint routinely needs several seconds per query and
    # LRCLIB has its own bad minutes; waiting out one before starting the
    # other is most of what keeps a new track on the hourglass. They run
    # together, and LrcAPI still wins whenever it has an answer.
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        lrcapi_lookup = pool.submit(choose_lrcapi_candidate, track)
        lrclib_lookup = pool.submit(lrclib_record, track)

        try:
            lrcapi = lrcapi_lookup.result()
        except Exception as exc:
            log_error(f"LrcAPI lookup failed; using LRCLIB: {exc}")
            lrcapi = None
        if lrcapi is not None:
            return lrcapi

        return lrclib_lookup.result()
    finally:
        # Never wait on the provider whose answer is no longer wanted: the
        # cache write that ends the hourglass happens as soon as this returns.
        pool.shutdown(wait=False)


def background_fetch(key: str, track: dict[str, Any]) -> None:
    lock = lock_path(key)
    started = time.monotonic()
    # Keep a small margin so retries and pending queries stop before SIGALRM.
    set_fetch_deadline(time.monotonic() + max(config.FETCH_TIMEOUT_SECONDS - 3.0, 1.0))
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
                    "status": "network_error",
                    "message": str(exc),
                    "fetched_at": now,
                    "retry_after": now + config.RETRY_NETWORK_ERROR_SECONDS,
                },
            )
            trace(
                "fetch", started, "network_error", reason=str(exc).split(":")[0][:40]
            )
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

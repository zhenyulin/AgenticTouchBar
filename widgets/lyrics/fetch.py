"""Reading Apple Music's cached lyrics in the detached fetch helper."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

from . import config
from .cache import atomic_write_json, cache_path, lock_path
from .catalog import catalog_chinese_identity
from .concurrency import set_fetch_deadline
from .locking import acquire_lock, clear_lock, spawn_helper
from .lrc import parse_lrc
from .output import log_error, trace
from .providers.apple_cache import apple_cache_record
from .providers.local import local_lyrics_record
from .providers.lrcapi import choose_lrcapi_candidate
from .providers.lrclib import lrclib_record
from .providers.netease import netease_record
from .providers.qqmusic import qqmusic_record


def fetch_lyrics_record(track: dict[str, Any]) -> dict[str, Any] | None:
    from concurrent.futures import ThreadPoolExecutor, TimeoutError

    local = local_lyrics_record(track)
    if local is not None:
        return local

    try:
        apple_cached = apple_cache_record(track)
    except Exception as exc:
        # The TTML cache is a fast path, not a library. A broken or locked
        # cache must not starve the remote providers, which may still have
        # the lyrics; the failure stays in the error log.
        log_error(f"Apple lyrics cache lookup failed: {exc}")
        apple_cached = None
    if apple_cached is not None:
        return apple_cached

    identity = catalog_chinese_identity(track)
    if identity is not None:
        search_title, search_artist = identity
        if search_title and search_title != track.get("title"):
            track = {**track, "search_title": search_title}
        if search_artist and search_artist != track.get("artist"):
            track = {**track, "search_artist": search_artist}

    pool = ThreadPoolExecutor(max_workers=4)
    try:
        qqmusic_future = pool.submit(qqmusic_record, track)
        netease_future = pool.submit(netease_record, track)
        lrcapi_future = pool.submit(choose_lrcapi_candidate, track)
        lrclib_future = pool.submit(lrclib_record, track)

        # QQ Music and NetEase carry genuinely timed LRC for most Chinese
        # tracks where LrcAPI is silent, so their answers decide first.
        # LrcAPI then gets a bounded preference window over LRCLIB, and the
        # remaining wait only if neither has anything.
        try:
            qqmusic = qqmusic_future.result()
        except Exception as qqmusic_exc:
            log_error(f"QQ Music lookup failed; using NetEase: {qqmusic_exc}")
            qqmusic = None

        if qqmusic is not None:
            return qqmusic

        try:
            netease = netease_future.result()
        except Exception as netease_exc:
            log_error(f"NetEase lookup failed; using LrcAPI: {netease_exc}")
            netease = None

        if netease is not None:
            return netease

        lrcapi_timed_out = False
        try:
            lrcapi = lrcapi_future.result(timeout=config.LRCAPI_PREFERENCE_SECONDS)
        except TimeoutError:
            lrcapi_timed_out = True
            lrcapi = None
        except Exception as exc:
            log_error(f"LrcAPI lookup failed; using LRCLIB: {exc}")
            lrcapi = None

        if lrcapi is not None:
            return lrcapi

        try:
            lrclib = lrclib_future.result()
        except Exception as lrclib_exc:
            if lrcapi_timed_out:
                # LrcAPI may still be running; its answer can still save the
                # fetch from a transient LRCLIB failure.
                try:
                    lrcapi = lrcapi_future.result()
                except Exception as lrcapi_exc:
                    log_error(f"LrcAPI lookup failed; using LRCLIB: {lrcapi_exc}")
                    raise lrclib_exc
                if lrcapi is not None:
                    return lrcapi
            raise lrclib_exc

        if lrclib is not None:
            return lrclib

        if not lrcapi_timed_out:
            return None

        try:
            return lrcapi_future.result()
        except Exception as exc:
            log_error(f"LrcAPI lookup failed; using LRCLIB: {exc}")
            return None
    finally:
        pool.shutdown(wait=False)


def background_fetch(key: str, track: dict[str, Any]) -> None:
    lock = lock_path(key)
    started = time.monotonic()
    set_fetch_deadline(time.monotonic() + max(config.FETCH_TIMEOUT_SECONDS - 3.0, 1.0))
    try:
        try:
            record = fetch_lyrics_record(track)
            now = time.time()
            if record is None:
                payload = {
                    "status": "not_found",
                    "cache_version": config.CACHE_KEY_VERSION,
                    "fetched_at": now,
                    "retry_after": now + config.RETRY_NOT_FOUND_SECONDS,
                }
            elif record.get("instrumental"):
                payload = {
                    "status": "instrumental",
                    "cache_version": config.CACHE_KEY_VERSION,
                    "fetched_at": now,
                    "retry_after": now + config.RETRY_NOT_FOUND_SECONDS,
                    "record": record,
                }
            else:
                record = dict(record)
                record["parsedLines"] = parse_lrc(record.get("syncedLyrics") or "")
                payload = {
                    "status": "ok",
                    "cache_version": config.CACHE_KEY_VERSION,
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
                    "cache_version": config.CACHE_KEY_VERSION,
                    "message": str(exc),
                    "fetched_at": now,
                    "retry_after": now + config.RETRY_CACHE_ERROR_SECONDS,
                },
            )
            trace("fetch", started, "cache_error", reason=str(exc).split(":")[0][:40])
    finally:
        set_fetch_deadline(None)
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

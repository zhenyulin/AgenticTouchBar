"""--diagnose and --clear-current: interactive troubleshooting for whatever
track is playing right now."""

from __future__ import annotations

import json

from . import config
from .apple_music import read_apple_music
from .cache import cache_path, lock_path
from .metadata import track_cache_key
from .providers.apple_cache import apple_cache_record
from .viewport import (
    lyric_width_px,
    now_playing_lines,
    now_playing_width_px,
)


def diagnose_current() -> int:
    try:
        track = read_apple_music()
    except Exception as exc:
        print(f"Apple Music error: {exc}")
        return 1

    if track.get("state") in {"not_running", "stopped"}:
        print(f"Music state: {track.get('state')}")
        return 1

    print(json.dumps(track, ensure_ascii=False, indent=2))
    print(f"cache key: {track_cache_key(track)}")
    now_playing_px = now_playing_width_px(track)
    lyric_px = lyric_width_px(now_playing_px)
    print(
        f"now playing rows: {now_playing_lines(track)} -> {now_playing_px:.0f}px, "
        f"leaving the lyric {lyric_px:.0f}px "
        f"({max(round(lyric_px / config.PIXELS_PER_CELL), 1)} cells) "
        f"of {config.LYRIC_WIDTH_BUDGET_PX:.0f}px"
    )
    print(f"Apple Music lyrics cache: {config.APPLE_MUSIC_CACHE_DB}")
    apple_cached = apple_cache_record(track)
    if apple_cached is not None:
        print(f"APPLE CACHE: {apple_cached.get('id')}")
        return 0
    print("No matching cached Apple Music TTML.")
    return 2


def clear_current_cache() -> int:
    try:
        track = read_apple_music()
        key = track_cache_key(track)
        for path in (cache_path(key), lock_path(key)):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        print(f"Cleared current track cache: {key}")
        return 0
    except Exception as exc:
        print(f"Could not clear current cache: {exc}")
        return 1

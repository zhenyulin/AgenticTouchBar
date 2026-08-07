"""Turning a track + cached lyrics into widget text, and the widget's main tick."""

from __future__ import annotations

import time
from typing import Any

from . import config
from .apple_music import _last_sample_age_ms, current_track, is_placeholder_track
from .cache import cache_path, read_cache
from .fetch import start_background_fetch
from .layout import crop_cells, current_lyric_line, display_width, marquee, wrap_lyric
from .locking import (
    acquire_widget_lock,
    fetch_waiting_seconds,
    release_widget_lock,
)
from .lrc import parse_lrc
from .metadata import track_cache_key
from .output import emit, emit_last_output, log_error, trace


def render_widget(
    track: dict[str, Any],
    cached: dict[str, Any] | None,
    waiting_seconds: float = 0.0,
) -> str:
    state = track.get("state", "")
    title = track.get("title", "") or "Apple Music"

    if cached is None:
        # An hourglass is only honest while a fetch could plausibly still
        # land. Past that it reads as a stuck widget, and the track title is
        # the more useful thing to leave on screen.
        marker = "⌛" if waiting_seconds <= config.PENDING_HOURGLASS_SECONDS else "♪"
        return f"{marker} {title}"

    status = cached.get("status")
    if status == "instrumental":
        return "♪"
    if status == "not_found":
        return "♪"
    if status == "network_error":
        return "⚠ Lyrics network error"
    if status != "ok":
        return "♪ Lyrics unavailable"

    record = cached.get("record") or {}
    synced_lyrics = record.get("syncedLyrics") or ""
    lines = record.get("parsedLines") or parse_lrc(synced_lyrics)
    position = float(track.get("position", 0.0)) + config.SYNC_OFFSET_SECONDS
    lyric, elapsed, index = current_lyric_line(lines, position)

    prefix = "Ⅱ " if state == "paused" else "♪ "
    if lyric is None:
        return prefix + title

    first_width = max(config.VIEWPORT_WIDTH - display_width(prefix), 8)
    other_width = max(
        config.VIEWPORT_WIDTH - display_width(config.CONTINUATION_INDENT), 8
    )
    rows = wrap_lyric(lyric, [first_width, other_width], config.MAX_LYRIC_ROWS)

    # A short current line leaves a spare row rather than wrapping into it.
    # Fill that row with the next lyric (static, not scrolled) as a preview,
    # so the widget still shows two rows instead of a blank second line.
    if len(rows) == 1 and config.MAX_LYRIC_ROWS > 1 and index + 1 < len(lines):
        next_lyric = lines[index + 1][1]
        if display_width(next_lyric) > other_width:
            next_lyric = crop_cells(next_lyric, 0, max(other_width - 1, 1)) + "…"
        return (
            prefix
            + marquee(rows[0], elapsed, first_width)
            + "\n"
            + config.CONTINUATION_INDENT
            + next_lyric
        )

    return "\n".join(
        (prefix if row_index == 0 else config.CONTINUATION_INDENT)
        + marquee(row, elapsed, first_width if row_index == 0 else other_width)
        for row_index, row in enumerate(rows)
    )


def render_tick() -> str:
    """Render one widget frame. Returns the path taken, for the trace."""
    track = current_track()

    if track is None:
        # Nothing fresh to render: a sampler is already on its way, so
        # hold the last frame rather than blanking the widget for a tick.
        emit_last_output()
        return "no_sample"

    state = track.get("state")
    if state == "denied":
        emit("⚠ Allow BTT → Music")
        return "denied"
    if state in {"not_running", "stopped"} or not track.get("title"):
        emit("♪")
        return "idle"

    if is_placeholder_track(track):
        # Apple Music has not settled on the real track yet, and its
        # placeholder is not something any lyrics provider knows.
        emit(f"♪ {track.get('title', '')}")
        return "placeholder"

    key = track_cache_key(track)
    cached = read_cache(key)
    now = time.time()

    if cached is None:
        try:
            start_background_fetch(key, track)
        except Exception as exc:
            log_error(f"Could not start background fetch: {exc}")
        emit(render_widget(track, None, fetch_waiting_seconds(key)))
        return "pending"

    retry_after = float(cached.get("retry_after", 0) or 0)
    if retry_after and now >= retry_after:
        try:
            cache_path(key).unlink()
        except OSError:
            pass
        try:
            start_background_fetch(key, track)
        except Exception as exc:
            log_error(f"Could not retry background fetch: {exc}")
        emit(render_widget(track, None, fetch_waiting_seconds(key)))
        return "retrying"

    emit(render_widget(track, cached))
    return cached.get("status") or "ok"


def widget_main() -> int:
    started = config.LOADED_AT

    if not acquire_widget_lock():
        # Two runs overlapping means ticks are taking longer than the widget's
        # interval. That is the shape of a freeze building, so it is worth a
        # line of its own rather than being lost inside the normal path.
        emit_last_output()
        trace("widget", started, "locked")
        return 0

    outcome = "crashed"
    try:
        outcome = render_tick()
        return 0
    finally:
        release_widget_lock()
        trace("widget", started, outcome, sample_age_ms=_last_sample_age_ms())

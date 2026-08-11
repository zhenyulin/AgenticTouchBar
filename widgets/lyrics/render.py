"""Turning a track + cached lyrics into widget text, and the widget's main tick."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from . import config
from .apple_music import _last_sample_age_ms, current_track, is_placeholder_track
from .cache import atomic_write_json, cache_path, read_compatible_cache
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
from .providers.apple_cache import apple_cache_record
from .viewport import ensure_viewport, viewport_cells

# What this tick is putting on the Touch Bar, filled in as the tick renders
# and written out once by widget_main. BetterTouchTool runs the widget as a
# fresh process every tick, so this is per-tick state rather than anything
# shared between runs.
_RECEIPT: dict[str, Any] = {"key": "", "state": "", "index": -1, "next_change_at": None}

# Outcomes whose frame is a reprint of the previous one (see
# emit_last_output), which deliberately leave the previous receipt and its
# deadline alone. This is the whole point of the receipt: a widget that
# reprints the last frame every second -- because the sampler died, or
# because its lock is held -- writes a trace line every second too, so it
# looks perfectly healthy right up until you glance at the Touch Bar. An
# untouched deadline sliding into the past is the only local evidence that
# the display has stopped moving.
#
# "pending" is not one of them: holding the previous lyric while a fetch for
# a new track runs is deliberate, bounded by FETCH_TIMEOUT_SECONDS, and would
# otherwise strand the previous track's deadline at every cache miss. It
# writes a receipt with no deadline instead, which reads as "nothing is due
# to change".
REPRINT_OUTCOMES = {"no_sample", "locked", "crashed"}


def marker_font_color(track: dict[str, Any]) -> str:
    """The BTT font_color for the status markers, fading with playback.

    A track with no lyrics keeps its symbol (♬ instrumental, ♩ not found)
    on screen for the whole track, so it dims from white down to a quiet
    gray as the track plays instead of staying a bright fixed symbol. The
    fade follows the playback position, not wall clock time, so a seek
    anywhere in the track lands on the matching shade. The value is the
    comma-separated r,g,b,a string BTT's widget JSON expects, with the
    widget's full alpha.
    """
    duration = float(track.get("duration", 0.0) or 0.0)
    position = float(track.get("position", 0.0) or 0.0)
    if duration <= 0:
        progress = 0.0
    else:
        progress = max(0.0, min(position / duration, 1.0))
    gray = round(
        config.MARKER_FADE_MAX
        + (config.MARKER_FADE_MIN - config.MARKER_FADE_MAX) * progress
    )
    return f"{gray},{gray},{gray},255"


def write_render_receipt(outcome: str) -> None:
    """Record what went on screen this tick, for the freeze guard to check."""
    if outcome in REPRINT_OUTCOMES:
        return

    payload = dict(_RECEIPT)
    payload["at"] = time.time()
    payload["outcome"] = outcome

    # Written whole, then renamed over the old one, so the guard reading it a
    # few times a minute never catches a half-written line.
    temporary = config.RENDER_PATH.with_name(f"render.tmp.{os.getpid()}")
    try:
        config.RENDER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(temporary, config.RENDER_PATH)
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass


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
        return "♬"
    if status == "not_found":
        return "♩"
    if status != "ok":
        # A cache_error is a provider failure; Apple-cache read problems are
        # logged and degrade to a miss, so the marker must not read as an
        # Apple Music problem -- least of all while Music is not running.
        return "♪ Lyrics unavailable"

    record = cached.get("record") or {}
    synced_lyrics = record.get("syncedLyrics") or ""
    lines = record.get("parsedLines") or parse_lrc(synced_lyrics)
    position = float(track.get("position", 0.0)) + config.SYNC_OFFSET_SECONDS
    lyric, elapsed, index = current_lyric_line(lines, position)

    # The one thing only this function knows: when the frame it is about to
    # return stops being correct. Recording it as a wall clock time lets the
    # freeze guard judge a stuck display without parsing any LRC -- and
    # judge it against the gap this track actually has, instead of a fixed
    # threshold that a long instrumental break trips for no reason.
    _RECEIPT["index"] = index
    if state == "playing" and index + 1 < len(lines):
        _RECEIPT["next_change_at"] = time.time() + max(
            lines[index + 1][0] - position, 0.0
        )

    prefix = (
        "Ⅱ "
        if state == "paused"
        else ("♪ " if record.get("source") == "apple-cache" else "♫ ")
    )
    if lyric is None:
        return prefix + title

    viewport = viewport_cells()
    first_width = max(viewport - display_width(prefix), 8)
    other_width = max(viewport - display_width(config.CONTINUATION_INDENT), 8)
    rows = wrap_lyric(lyric, [first_width, other_width], config.MAX_LYRIC_ROWS)

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
    _RECEIPT["state"] = state or ""
    if state == "denied":
        emit("⚠ Allow BTT → Music")
        return "denied"
    if state == "paused":
        emit("")
        return "paused"
    if state in {"not_running", "stopped"} or not track.get("title"):
        # BetterTouchTool hides script widgets whose text is empty. Match the
        # native Now Playing widget when Music has been quit instead of
        # leaving a stray music-note button behind.
        emit("")
        return "idle"

    if is_placeholder_track(track):
        # Apple Music has not settled on the real track yet, and its
        # placeholder is not something any lyrics provider knows.
        emit(f"♪ {track.get('title', '')}")
        return "placeholder"

    key = track_cache_key(track)
    _RECEIPT["key"] = key
    # How wide the Now Playing widget beside this one has grown depends on
    # the track, so the room left for the lyric is re-measured whenever the
    # track changes -- in the background, never in this tick.
    ensure_viewport(key, track)
    cached = read_compatible_cache(key, track)
    now = time.time()

    if cached is not None and cached.get("status") == "not_found":
        try:
            apple_record = apple_cache_record(track)
        except Exception as exc:
            log_error(f"Apple lyrics cache lookup failed: {exc}")
            apple_record = None
        if apple_record is not None:
            apple_record = dict(apple_record)
            apple_record["parsedLines"] = parse_lrc(
                apple_record.get("syncedLyrics") or ""
            )
            cached = {"status": "ok", "fetched_at": now, "record": apple_record}
            atomic_write_json(cache_path(key), cached)

    if cached is None:
        try:
            start_background_fetch(key, track)
        except Exception as exc:
            log_error(f"Could not start background fetch: {exc}")
        # A lookup happens only at a track boundary. Preserve the preceding
        # lyric frame until it completes, rather than replacing it with an
        # intermediate pending state that makes the Touch Bar appear blank.
        emit_last_output()
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

    emit(
        render_widget(track, cached),
        font_color=(
            marker_font_color(track)
            if cached.get("status") in {"instrumental", "not_found"}
            else None
        ),
    )
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
        write_render_receipt(outcome)
        trace("widget", started, outcome, sample_age_ms=_last_sample_age_ms())

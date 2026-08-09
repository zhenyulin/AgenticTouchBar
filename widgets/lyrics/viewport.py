"""How much of the Touch Bar row the lyric may take, given what the Now
Playing widget beside it is currently drawing.

BetterTouchTool has no API for a laid-out widget's width -- `get_trigger`
returns an item's configuration, not its geometry -- so this derives it from
the same inputs BTT renders from: the Now Playing widget's two format
strings, filled in with the playing track and measured in the font BTT draws
them in. Everything else on the row is fixed width, so whatever those two
lines leave over belongs to the lyric, out of the budget the pair share
(config.LYRIC_WIDTH_BUDGET_PX).

Measuring costs an osascript launch, roughly 200 ms. That is a fifth of a
widget tick and has no business on the widget path -- but it only changes
when the track does. So a tick that notices a new track claims it at the
width the previous track used and hands the measurement to a detached
helper, which overwrites the claim a tick or so later.
"""

from __future__ import annotations

import base64
import json
import string
import subprocess
import time
from functools import cache
from pathlib import Path
from typing import Any

from . import config
from .cache import atomic_write_json
from .locking import spawn_helper
from .output import log_error, trace

MEASURE_SCRIPT = Path(__file__).resolve().with_name("text_width.js")
CHARACTER_METRICS_VERSION = 2
MEASURED_CHARACTERS = " " + string.ascii_letters + string.digits + string.punctuation


class _TrackFields(dict):
    """Format fields for the Now Playing line formats, tolerating unknowns.

    BTT accepts more placeholders than this widget samples ({composer} and
    friends). One of those appearing in a mirrored format string should cost
    that placeholder's own width, not the whole measurement.
    """

    def __missing__(self, key: str) -> str:
        return ""


def now_playing_lines(track: dict[str, Any]) -> list[str]:
    """The rows the Now Playing widget is drawing for this track."""
    fields = _TrackFields(
        title=str(track.get("title", "") or ""),
        album=str(track.get("album", "") or ""),
        artist=str(track.get("artist", "") or ""),
        genre=str(track.get("genre", "") or ""),
    )
    return [
        line_format.format_map(fields)[: config.NOW_PLAYING_LINE_MAX_CHARS]
        for line_format in config.NOW_PLAYING_LINE_FORMATS
    ]


def measure_px(strings: list[str], font_size: float) -> list[float]:
    """Rendered widths in points, via the Cocoa text layout BTT itself uses."""
    request = json.dumps(
        {"fontSize": font_size, "strings": strings}, ensure_ascii=False
    )
    result = subprocess.run(
        ["/usr/bin/osascript", "-l", "JavaScript", str(MEASURE_SCRIPT), request],
        capture_output=True,
        text=True,
        timeout=config.MEASURE_TIMEOUT_SECONDS,
        check=True,
    )
    widths = [float(line) for line in result.stdout.split()]
    if len(widths) != len(strings):
        raise ValueError(f"measured {len(widths)} of {len(strings)} strings")
    return widths


def measure_track(track: dict[str, Any]) -> tuple[list[float], dict[str, float]]:
    """Measure Now Playing rows and lyric-font glyph advances."""
    lines = now_playing_lines(track)
    characters = list(dict.fromkeys(MEASURED_CHARACTERS + "".join(lines)))
    row_widths = measure_px(lines, config.NOW_PLAYING_FONT_SIZE)
    character_widths = measure_px(characters, config.LYRICS_FONT_SIZE)
    return row_widths, dict(zip(characters, character_widths))


def now_playing_width_px(track: dict[str, Any]) -> float:
    """How wide the Now Playing widget's text is for this track.

    The wider of its two rows is what sets the widget's width, and BTT stops
    widening it at BTTTBWidgetWidth -- past that the title truncates instead
    of taking any more of the row, so neither does the number here.
    """
    widths = measure_px(now_playing_lines(track), config.NOW_PLAYING_FONT_SIZE)
    return min(max(widths), config.NOW_PLAYING_MAX_TEXT_PX)


def lyric_width_px(now_playing_px: float) -> float:
    """The lyric's share of the budget the two widgets divide between them."""
    return min(
        max(
            config.LYRIC_WIDTH_BUDGET_PX - now_playing_px,
            config.MIN_LYRIC_WIDTH_PX,
        ),
        config.MAX_LYRIC_WIDTH_PX,
    )


@cache
def read_viewport() -> dict[str, Any]:
    """The stored width. Cached: BTT runs a fresh process every tick, so this
    is read at most once per frame anyway, and never goes stale within one."""
    try:
        with config.VIEWPORT_PATH.open("r", encoding="utf-8") as handle:
            stored = json.load(handle)
        return stored if isinstance(stored, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def stored_lyric_px(stored: dict[str, Any]) -> float | None:
    try:
        return float(stored["lyric_px"])
    except (KeyError, TypeError, ValueError):
        return None


def stored_character_width(character: str) -> float | None:
    metrics = read_viewport().get("character_widths")
    if not isinstance(metrics, dict):
        return None
    try:
        return float(metrics[character])
    except (KeyError, TypeError, ValueError):
        return None


def write_viewport(payload: dict[str, Any]) -> None:
    try:
        atomic_write_json(config.VIEWPORT_PATH, payload)
    except OSError as exc:
        log_error(f"Could not write {config.VIEWPORT_PATH}: {exc}")
    read_viewport.cache_clear()


def viewport_cells() -> int:
    """The width this frame may draw into, in layout cells."""
    if config.VIEWPORT_WIDTH_OVERRIDE is not None:
        return config.VIEWPORT_WIDTH_OVERRIDE
    lyric_px = stored_lyric_px(read_viewport())
    if lyric_px is None:
        lyric_px = config.FALLBACK_LYRIC_WIDTH_PX
    return max(round(lyric_px / config.PIXELS_PER_CELL), 1)


def ensure_viewport(key: str, track: dict[str, Any]) -> None:
    """Keep the stored width in step with the playing track.

    On the widget path, so it measures nothing itself: it claims the new
    track at the width the previous one used and leaves the measuring to a
    detached helper. Writing that claim before spawning is what stops every
    later tick from spawning a helper of its own -- including when the helper
    fails, where carrying the previous width forward is the right answer
    anyway.
    """
    if config.VIEWPORT_WIDTH_OVERRIDE is not None:
        return

    stored = read_viewport()
    if (
        stored.get("key") == key
        and stored.get("metrics_version") == CHARACTER_METRICS_VERSION
    ):
        return

    carried = stored_lyric_px(stored)
    if carried is None:
        carried = config.FALLBACK_LYRIC_WIDTH_PX
    write_viewport(
        {
            "key": key,
            "lyric_px": carried,
            "measured": False,
            "at": time.time(),
        }
    )
    try:
        spawn_helper(["--measure", key, encode_track(track)])
    except Exception as exc:
        log_error(f"Could not start viewport measurement: {exc}")


def encode_track(track: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(track, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")


def store_measured_viewport(key: str, track: dict[str, Any]) -> None:
    """Measure this track and record the lyric's share of the row."""
    started = time.monotonic()
    row_widths, character_widths = measure_track(track)
    now_playing_px = min(max(row_widths), config.NOW_PLAYING_MAX_TEXT_PX)
    lyric_px = lyric_width_px(now_playing_px)

    # The track can change again while osascript is starting up. Landing a
    # stale measurement over the newer track's claim would leave the wrong
    # width in place until the track after that.
    if read_viewport().get("key") != key:
        trace("viewport", started, "superseded")
        return

    write_viewport(
        {
            "key": key,
            "lyric_px": lyric_px,
            "now_playing_px": now_playing_px,
            "now_playing_rows_px": row_widths,
            "character_widths": character_widths,
            "metrics_version": CHARACTER_METRICS_VERSION,
            "measured": True,
            "at": time.time(),
        }
    )
    trace(
        "viewport",
        started,
        "ok",
        now_playing_px=f"{now_playing_px:.0f}",
        lyric_px=f"{lyric_px:.0f}",
    )

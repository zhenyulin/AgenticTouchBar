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
import re
import string
import subprocess
import time
from functools import cache
from pathlib import Path
from typing import Any

from .. import config
from ..runtime.cache import atomic_write_json
from .layout import display_width
from ..runtime.locking import spawn_helper
from ..runtime.output import log_error, trace

MEASURE_SCRIPT = Path(__file__).resolve().with_name("text_width.js")
# Bumped whenever a stored measurement stops meaning what it did, so the
# playing track is measured again instead of carrying the old numbers until
# it changes. Version 4: the Now Playing rows moved to 13 pt / 10.5 pt.
CHARACTER_METRICS_VERSION = 4
MEASURED_CHARACTERS = " " + string.ascii_letters + string.digits + string.punctuation


class _TrackFields(dict):
    """Format fields for the Now Playing line formats, tolerating unknowns.

    BTT accepts more placeholders than this widget samples ({composer} and
    friends). One of those appearing in a mirrored format string should cost
    that placeholder's own width, not the whole measurement.
    """

    def __missing__(self, key: str) -> str:
        return ""


_PARENS_STRIP_RE = re.compile(r"\([^)]*\)")
_WS_COLLAPSE_RE = re.compile(r"\s+")


def strip_parens(text: str) -> str:
    """ "Song (feat. X)" -> "Song", collapsing the leftover gap.

    The now-playing widget (widgets/now-playing.sh) strips the same way, so
    the measured width stays the width the widget actually draws.
    """
    return _WS_COLLAPSE_RE.sub(" ", _PARENS_STRIP_RE.sub("", text)).strip()


def display_album(text: str) -> str:
    """Only the first work of a multi-work album, e.g. "X; Y" -> "X".

    The now-playing widget (widgets/now-playing.sh) shows the same, so the
    measured width stays the width the widget actually draws.
    """
    return strip_parens(text).split(";")[0].strip()


def now_playing_lines(track: dict[str, Any]) -> list[str]:
    """The rows the Now Playing widget is drawing for this track."""
    fields = _TrackFields(
        title=strip_parens(str(track.get("title", "") or "")),
        album=display_album(str(track.get("album", "") or "")),
        artist=str(track.get("artist", "") or ""),
    )
    return [
        line_format.format_map(fields) for line_format in _now_playing_formats(fields)
    ]


def _row_extent(formats: tuple[str, str], fields: _TrackFields) -> int:
    """How long a layout's longest row is, in characters.

    That row is what sets the widget's width. widgets/now-playing.sh picks
    its layout on the same count -- it runs on BTT's tick and cannot afford
    the Cocoa measurement below -- so this counts rather than measures, or
    the two would choose different rows and the measured width would stop
    being the drawn one.
    """
    return max(len(line_format.format_map(fields)) for line_format in formats)


def _now_playing_formats(fields: _TrackFields) -> tuple[str, str]:
    """The row formats the widget draws, balanced when they are the stock pair.

    widgets/now-playing.sh swaps between ({album} ▸ {title}, {artist}) and
    ({title}, {artist} ▸ {album}) on whichever keeps its widest row
    narrower -- that row is what the widget's width comes to, so the
    narrower one leaves more of the pair's budget for the lyric. This
    mirrors that decision so the measured width stays the width the widget
    actually draws. Custom formats always win.
    """
    line1_fmt, line2_fmt = config.NOW_PLAYING_LINE_FORMATS
    stock = (line1_fmt, line2_fmt)
    if (
        line1_fmt == "{album} ▸ {title}"
        and line2_fmt == "{artist}"
        and fields.get("artist")
    ):
        balanced = ("{title}", "{artist} ▸ {album}")
        if _row_extent(balanced, fields) < _row_extent(stock, fields):
            line1_fmt, line2_fmt = balanced
    return line1_fmt, line2_fmt


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


def row_font_sizes(count: int) -> list[float]:
    """The font size BTT draws each of a widget's rows at.

    The first row gets the size the preset sets; every row below it is drawn
    smaller, so that all of them fit the widget's fixed height (see
    config.SECOND_ROW_FONT_SIZE).
    """
    return [
        config.NOW_PLAYING_FONT_SIZE if index == 0 else config.SECOND_ROW_FONT_SIZE
        for index in range(count)
    ]


def measure_rows(lines: list[str]) -> list[float]:
    """Rendered widths of the Now Playing rows, each in its own row's font.

    One osascript launch per row rather than one for all of them, because
    the rows are drawn at different sizes. Only the detached measurement
    helper reaches this, so the extra ~200 ms costs the widget path nothing.
    """
    return [
        measure_px([line], font_size)[0]
        for line, font_size in zip(lines, row_font_sizes(len(lines)))
    ]


def measure_track(track: dict[str, Any]) -> tuple[list[float], dict[str, float]]:
    """Measure Now Playing rows and lyric-font glyph advances."""
    lines = now_playing_lines(track)
    characters = list(dict.fromkeys(MEASURED_CHARACTERS + "".join(lines)))
    row_widths = measure_rows(lines)
    character_widths = measure_px(characters, config.LYRICS_FONT_SIZE)
    return row_widths, dict(zip(characters, character_widths))


def now_playing_width_px(track: dict[str, Any]) -> float:
    """How wide the Now Playing widget's text is for this track.

    The wider of its two rows is what sets the widget's width, and BTT stops
    widening it at BTTTBWidgetWidth -- past that the title truncates instead
    of taking any more of the row, so neither does the number here. The two
    rows are measured in their own fonts (measure_rows), the second being
    the smaller of the pair.
    """
    widths = measure_rows(now_playing_lines(track))
    return min(max(widths), config.NOW_PLAYING_MAX_TEXT_PX)


def lyric_budget_px(track: dict[str, Any]) -> float:
    """What the Now Playing + lyric pair may take for this track.

    The Star widget (★/☆) only renders while Apple Music plays; with any
    other player it draws empty and BTT hides it, leaving the pair one
        row slot (~95 px) more of the row. A track without a source marker
        reads as Apple Music, so the extra never applies to unknown samples.
    """
    budget = config.LYRIC_WIDTH_BUDGET_PX
    if track.get("source", "apple_music") != "apple_music":
        budget += config.LYRIC_EXTRA_WORD_PX
    return budget


def lyric_width_px(now_playing_px: float, budget_px: float) -> float:
    """The lyric's share of the budget the two widgets divide between them."""
    return min(
        max(
            budget_px - now_playing_px,
            config.MIN_LYRIC_WIDTH_PX,
        ),
        config.MAX_LYRIC_WIDTH_PX,
    )


def opencode_rows() -> list[str]:
    """The rows the OpenCode quota widget is drawing right now."""
    try:
        text = config.OPENCODE_VALUE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        text = config.OPENCODE_DEFAULT_TEXT
    rows = [row for row in text.split("\n") if row][:2]
    return rows or [config.OPENCODE_DEFAULT_TEXT.split("\n")[0]]


def opencode_text_px() -> float:
    """The widest OpenCode row, in points at the widget's own font size.

    Scaled from the lyric font's stored glyph advances: the two widgets use
    the same system font at different sizes, and Cocoa metrics scale
    linearly.
    """
    scale = config.OPENCODE_FONT_SIZE / config.LYRICS_FONT_SIZE
    return max(display_width(row) for row in opencode_rows()) * (
        config.PIXELS_PER_CELL * scale
    )


def opencode_slot_px() -> float:
    """The row slot the OpenCode widget occupies: text, icon and gaps.

    The icon (BTTTouchBarItemIconWidth 22) and the gap after it
    (BTTTouchBarIconTextOffset 5) add to the widest text row; the widget's
    own negative item padding and free space pull its neighbours in, so
    they subtract (bttpreset/Default.bttpreset).
    """
    return (
        opencode_text_px()
        + config.OPENCODE_ICON_PX
        + config.OPENCODE_ICON_OFFSET_PX
        + config.OPENCODE_ITEM_PADDING_PX
        + config.OPENCODE_FREE_SPACE_PX
    )


def effective_budget_px(track: dict[str, Any]) -> float:
    """The pair's budget right now, OpenCode slot included when hidden.

    While the pair is cramped the lyrics widget hides the OpenCode widget,
    so the pair may take that much more of the row. The hide flag is the
    same signal the OpenCode widget's own tick honours, which keeps the
    two sides of the bargain in step.
    """
    budget = lyric_budget_px(track)
    if config.OPENCODE_HIDE_ENABLED and config.OPENCODE_HIDE_PATH.is_file():
        budget += opencode_slot_px()
    return budget


def pair_short_of_space(track: dict[str, Any], key: str) -> bool:
    """True while the Now Playing + Lyrics pair is cramped on the row.

    The lyrics widget hides the OpenCode widget for as long as this holds
    (render.update_opencode_visibility). Two signs, both from the stored
    measurement: a Now Playing row whose text is past the visible budget
    (BTT truncates it), or a pair that cannot give the lyric its minimum
    width. Before the current track has been measured, the width carried
    from the previous one is the only guide: a lyric squeezed to its floor
    says the pair was cramped.
    """
    stored = read_viewport()
    lyric_px = stored_lyric_px(stored)
    if stored.get("key") != key:
        return lyric_px is not None and lyric_px <= config.MIN_LYRIC_WIDTH_PX

    rows = stored.get("now_playing_rows_px")
    if not (isinstance(rows, list) and rows):
        return lyric_px is not None and lyric_px <= config.MIN_LYRIC_WIDTH_PX
    if any(float(width) > config.NOW_PLAYING_MAX_TEXT_PX for width in rows):
        return True
    now_playing_px = stored.get("now_playing_px")
    return isinstance(
        now_playing_px, (int, float)
    ) and now_playing_px + config.MIN_LYRIC_WIDTH_PX > lyric_budget_px(track)


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
    budget = effective_budget_px(track)
    if (
        stored.get("key") == key
        and stored.get("metrics_version") == CHARACTER_METRICS_VERSION
        and stored.get("budget_px") == budget
    ):
        return

    carried = stored_lyric_px(stored)
    if carried is None:
        carried = config.FALLBACK_LYRIC_WIDTH_PX
    write_viewport(
        {
            "key": key,
            "lyric_px": carried,
            "budget_px": budget,
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
    budget_px = effective_budget_px(track)
    lyric_px = lyric_width_px(now_playing_px, budget_px)

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
            "budget_px": budget_px,
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

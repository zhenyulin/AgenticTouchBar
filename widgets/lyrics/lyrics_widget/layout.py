"""Fitting a lyric line into the Touch Bar's fixed-width, proportional-font
viewport: measuring, cropping, wrapping onto extra rows, and scrolling."""

from __future__ import annotations

import bisect
import unicodedata

from . import config


def character_width(character: str) -> int:
    if unicodedata.combining(character):
        return 0
    return 2 if unicodedata.east_asian_width(character) in {"W", "F", "A"} else 1


def display_width(text: str) -> int:
    return sum(character_width(character) for character in text)


def crop_cells(text: str, start: int, width: int) -> str:
    current = 0
    used = 0
    output: list[str] = []
    for character in text:
        char_width = character_width(character)
        next_position = current + char_width
        if next_position <= start:
            current = next_position
            continue
        if current < start < next_position:
            current = next_position
            continue
        if used + char_width > width:
            break
        output.append(character)
        used += char_width
        current = next_position
    return "".join(output).strip()


def break_positions(text: str, break_chars: str) -> list[int]:
    """Offsets just past each delimiter, i.e. the places a row may end."""
    return [
        index + 1 for index, character in enumerate(text) if character in break_chars
    ]


def row_width(widths: list[int], index: int) -> int:
    return widths[min(index, len(widths) - 1)]


def rows_overflow(rows: list[str], widths: list[int]) -> int:
    """Cells by which the worst row exceeds its budget; <= 0 means all fit."""
    return max(
        display_width(row) - row_width(widths, index) for index, row in enumerate(rows)
    )


def wrap_at_breaks(
    text: str, widths: list[int], max_rows: int, break_chars: str
) -> list[str]:
    """Wrap an over-wide line onto at most max_rows rows at break_chars.

    Returns a single row when the text already fits or has no delimiter, so
    such lines keep the previous scrolling behaviour.
    """
    breaks = break_positions(text, break_chars)
    if not breaks or max_rows <= 1:
        return [text]

    rows: list[str] = []
    start = 0
    while start < len(text):
        width = row_width(widths, len(rows))
        remainder = text[start:].strip()
        if not remainder:
            break
        # Last permitted row, or the rest already fits: take it whole.
        if len(rows) == max_rows - 1 or display_width(remainder) <= width:
            rows.append(remainder)
            break
        usable = [position for position in breaks if position > start]
        if not usable:
            rows.append(remainder)
            break
        # Prefer the latest comma that still fits; otherwise break at the
        # first one and let the row scroll.
        fitting = [
            position
            for position in usable
            if display_width(text[start:position].strip()) <= width
        ]
        cut = fitting[-1] if fitting else usable[0]
        rows.append(text[start:cut].strip())
        start = cut

    return [row for row in rows if row] or [text]


def wrap_lyric(text: str, widths: list[int], max_rows: int) -> list[str]:
    """Wrap at punctuation, falling back to spaces only when that is not enough.

    Punctuation marks phrase ends, so it is the better break when it fits.
    Spaces rescue the many LRC files that separate phrases without commas.
    """
    if display_width(text) <= row_width(widths, 0):
        return [text]

    tiers = [config.LINE_BREAK_PUNCTUATION]
    if config.BREAK_ON_SPACE and " " not in config.LINE_BREAK_PUNCTUATION:
        tiers.append(config.LINE_BREAK_PUNCTUATION + " ")

    best: list[str] | None = None
    best_overflow = 0
    for break_chars in tiers:
        rows = wrap_at_breaks(text, widths, max_rows, break_chars)
        overflow = rows_overflow(rows, widths)
        if overflow <= 0:
            return rows
        # Keep the earliest tier that comes closest; ties favour punctuation.
        if best is None or overflow < best_overflow:
            best, best_overflow = rows, overflow
    return best or [text]


def marquee(text: str, elapsed: float, width: int) -> str:
    if width <= 0 or display_width(text) <= width:
        return text
    if not config.SCROLL_LONG_LINES:
        return crop_cells(text, 0, max(width - 1, 1)) + "…"

    maximum_offset = max(display_width(text) - width, 0)
    moving_time = max(0.0, elapsed - config.SCROLL_DELAY_SECONDS)
    offset = min(int(moving_time * config.SCROLL_CELLS_PER_SECOND), maximum_offset)
    return crop_cells(text, offset, width)


def current_lyric_line(
    lines: list[tuple[float, str]], position: float
) -> tuple[str | None, float, int]:
    if not lines:
        return None, 0.0, -1
    timestamps = [item[0] for item in lines]
    index = bisect.bisect_right(timestamps, position) - 1
    if index < 0:
        return None, 0.0, -1
    timestamp, text = lines[index]
    return text, max(position - timestamp, 0.0), index

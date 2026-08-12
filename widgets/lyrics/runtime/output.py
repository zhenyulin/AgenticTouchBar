"""Everything the widget path uses to print, remember, and log a run."""

from __future__ import annotations

import json
import os
import time
from typing import Any

from .. import config


def emit(text: str, font_color: str | None = None) -> None:
    """Print the widget text for BetterTouchTool, one row per line.

    Each row is whitespace-compacted independently so a wrapped lyric keeps its
    leading indent; every other message is still a single row.

    A font_color turns the frame into the widget JSON BTT's script widgets
    parse -- {"text": ..., "font_color": "r,g,b,a"} -- the same shape the
    shell widgets emit via btt__emit_json (see widgets/lib/btt-widget.sh).
    HTML color tags are not a BTT mechanism, so this JSON is the only way a
    frame gets a color of its own; without one the widget renders in the
    font color configured in the preset.
    """
    if font_color is not None:
        output = json.dumps(
            {"text": text, "font_color": font_color}, ensure_ascii=False
        )
        print(output)
        remember_output(output)
        return

    rows: list[str] = []
    for raw_row in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        indent = raw_row[: len(raw_row) - len(raw_row.lstrip(" "))]
        body = " ".join(raw_row.split())
        if body:
            rows.append(indent + body)
    # An empty result is meaningful: BetterTouchTool removes a script widget
    # from the Touch Bar when its text is empty. Keep that distinction from a
    # genuinely rendered fallback value so Lyrics follows Now Playing when
    # Music quits.
    output = "\n".join(rows)
    print(output)
    remember_output(output)


def remember_output(text: str) -> None:
    """Keep the last printed value, so a skipped run can reprint it.

    A run that cannot take the widget lock has nothing of its own to show;
    printing this beats blanking the widget for a tick.
    """
    try:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        config.LAST_TEXT_PATH.write_text(text, encoding="utf-8")
        config.VALUE_PATH.write_text(text, encoding="utf-8")
    except OSError:
        pass


def emit_last_output() -> None:
    """Reprint the previous value, for a run with nothing of its own to show."""
    try:
        previous = config.LAST_TEXT_PATH.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        previous = ""
    output = previous or "♪"
    print(output)
    remember_output(output)

    # Touched even though the text is unchanged, so that this file's age
    # always means "how long since BetterTouchTool last ran the widget".
    # Without this a widget that reprints every tick is indistinguishable
    # from one BTT has stopped running, which is the single most useful
    # thing to know when the Touch Bar appears frozen.
    try:
        os.utime(config.LAST_TEXT_PATH, None)
    except OSError:
        pass


def trace(mode: str, started: float, outcome: str, **fields: Any) -> None:
    """Append one line describing a run, for --report to reconstruct later.

    A frozen Touch Bar looks the same whatever caused it, and the evidence is
    gone by the time anyone looks. So every run records that it happened, how
    long it took and which path it took. The gaps between lines are the most
    valuable part: they are the runs BetterTouchTool did not make.

    This must stay cheap. It is on the widget path, which runs every second
    and exists precisely so that nothing blocks there: one buffered append,
    no stat, no flush of anything else.
    """
    if not config.TRACE_ENABLED:
        return

    elapsed_ms = (time.monotonic() - started) * 1000.0
    extra = " ".join(f"{key}={value}" for key, value in fields.items())
    # Same columns as lib/btt-widget.sh writes, so one --report covers every
    # widget side by side and shows whether a problem is one script's or
    # shared across them.
    line = f"{time.time():.3f}\tlyrics\t{mode}\t{elapsed_ms:.0f}\t{outcome}\t{extra}\n"

    oversized = False
    try:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with config.TRACE_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line)
            # tell() after the write gives the size without a second syscall.
            oversized = handle.tell() > config.TRACE_MAX_BYTES
    except OSError:
        return

    if oversized:
        # One generation kept, so the trace costs at most twice the cap and
        # recent history stays inspectable just after a rotation.
        try:
            os.replace(
                config.TRACE_PATH,
                config.TRACE_PATH.with_name(config.TRACE_PATH.name + ".1"),
            )
        except OSError:
            pass


def log_error(message: str) -> None:
    try:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with (config.CACHE_DIR / "error.log").open("a", encoding="utf-8") as handle:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            handle.write(f"[{stamp}] {message}\n")
    except Exception:
        pass

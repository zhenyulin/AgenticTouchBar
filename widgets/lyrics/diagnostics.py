"""--watch and --report: reconstructing what BetterTouchTool was doing from
the trace files every run appends to."""

from __future__ import annotations

import subprocess
import time
from typing import NamedTuple

from . import config


def watch_mode(arguments: list[str]) -> int:
    """Record whether BetterTouchTool itself is alive, alongside the trace.

    The trace can show that no widget run happened, but not why. Three causes
    look identical from the Touch Bar and are told apart only by pairing a gap
    in the trace with what BTT was doing at that moment:

      BTT answers, runner idle  -> BTT stopped scheduling the widget
      BTT does not answer       -> BTT itself is wedged
      no gap at all             -> the widget ran; only the paint is stuck

    Run this in a terminal and leave it; --report folds it in. It is opt-in
    because it costs an AppleEvent every couple of seconds, which is far too
    expensive to put on the widget's own path.
    """
    try:
        interval = float(arguments[0]) if arguments else 2.0
    except ValueError:
        print("usage: --watch [seconds]")
        return 2

    probe = ['/usr/bin/osascript', '-e',
             'tell application "BetterTouchTool" to get_string_variable "__probe__"']
    print(f"Watching BTT every {interval:g}s → {config.WATCH_PATH}\nCtrl-C to stop.")

    try:
        while True:
            started = time.monotonic()
            try:
                completed = subprocess.run(
                    probe, capture_output=True, timeout=8, check=False
                )
                answer = "ok" if completed.returncode == 0 else "err"
            except subprocess.TimeoutExpired:
                answer = "TIMEOUT"
            except OSError:
                answer = "fail"
            took = (time.monotonic() - started) * 1000.0

            try:
                with config.WATCH_PATH.open("a", encoding="utf-8") as handle:
                    handle.write(f"{time.time():.3f}\t{answer}\t{took:.0f}\n")
            except OSError:
                pass

            if answer != "ok" or took > 2000:
                print(f"  {time.strftime('%H:%M:%S')}  BTT {answer} after {took:.0f} ms")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nstopped")
        return 0


def read_watch(minutes: float) -> list[tuple[float, str, float]]:
    cutoff = time.time() - minutes * 60.0
    rows: list[tuple[float, str, float]] = []
    try:
        content = config.WATCH_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rows
    for line in content.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        try:
            stamp, took = float(parts[0]), float(parts[2])
        except ValueError:
            continue
        if stamp >= cutoff:
            rows.append((stamp, parts[1], took))
    return rows


def freeze_verdict(
    watched: list[tuple[float, str, float]], start: float, end: float
) -> str:
    """Name the cause of one gap, from what BTT was doing during it."""
    during = [row for row in watched if start <= row[0] <= end]
    if not during:
        return "(not watched)"
    unanswered = [row for row in during if row[1] != "ok"]
    slow = [row for row in during if row[2] > 2000]
    if unanswered:
        return f"→ BTT ITSELF WEDGED ({len(unanswered)}/{len(during)} probes unanswered)"
    if slow:
        return f"→ BTT struggling ({len(slow)} probes over 2s)"
    return "→ BTT healthy, it simply stopped scheduling the widget"


class Row(NamedTuple):
    at: float
    widget: str
    mode: str
    elapsed_ms: float
    outcome: str
    extra: str


def read_trace(minutes: float) -> list[Row]:
    """Trace rows from the last `minutes`, oldest first, every widget."""
    cutoff = time.time() - minutes * 60.0
    rows: list[Row] = []

    sources = [
        config.TRACE_PATH.with_name(config.TRACE_PATH.name + ".1"),
        config.TRACE_PATH,
        config.SHELL_TRACE_PATH.with_name(config.SHELL_TRACE_PATH.name + ".1"),
        config.SHELL_TRACE_PATH,
    ]
    for path in sources:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in content.splitlines():
            parts = line.split("\t")
            if len(parts) < 5:
                continue
            try:
                stamp, elapsed = float(parts[0]), float(parts[3])
            except ValueError:
                continue
            if stamp >= cutoff:
                rows.append(
                    Row(
                        stamp,
                        parts[1],
                        parts[2],
                        elapsed,
                        parts[4],
                        parts[5] if len(parts) > 5 else "",
                    )
                )

    rows.sort(key=lambda row: row.at)
    return rows


def stamp_of(value: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(value))


def tally(rows: list[Row]) -> str:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.outcome] = counts.get(row.outcome, 0) + 1
    return "  ".join(
        f"{name} {count}"
        for name, count in sorted(counts.items(), key=lambda pair: -pair[1])
    )


def percentile_interval(ticks: list[Row], fraction: float) -> float:
    if len(ticks) < 2:
        return 1.0
    deltas = sorted(b.at - a.at for a, b in zip(ticks, ticks[1:]))
    index = min(int(len(deltas) * fraction), len(deltas) - 1)
    return max(deltas[index], 0.1)


def median_interval(ticks: list[Row]) -> float:
    return percentile_interval(ticks, 0.5)


def report_widget(name: str, rows: list[Row], watched: list[tuple[float, str, float]]) -> None:
    """One widget's ticks: how long they took, and where they stopped."""
    ticks = [row for row in rows if row.mode == "widget"]
    if not ticks:
        return

    elapsed = sorted(row.elapsed_ms for row in ticks)
    interval = median_interval(ticks)
    print(f"\n=== {name} ===")
    print(
        f"  {len(ticks)} runs, median {elapsed[len(elapsed) // 2]:.0f} ms, "
        f"slowest {elapsed[-1]:.0f} ms, about every {interval:.1f}s"
    )
    print(f"  outcomes: {tally(ticks)}")

    overlapped = sum(1 for row in ticks if row.outcome == "locked")
    if overlapped:
        print(
            f"  !! {overlapped} runs overlapped the previous one — this widget's "
            "ticks are outlasting its interval, which is how a freeze starts"
        )

    # A gap only means a freeze relative to how often this widget normally
    # runs. The threshold comes off the tail of its own intervals rather than
    # the median, so that a burst of manual runs -- which drags the median far
    # below the configured interval -- cannot make ordinary ticks look like
    # freezes.
    threshold = max(percentile_interval(ticks, 0.9) * 2.0, 3.0)
    gaps = [
        (previous.at, current.at - previous.at)
        for previous, current in zip(ticks, ticks[1:])
        if current.at - previous.at > threshold
    ]
    print(f"  gaps over {threshold:.0f}s: {len(gaps)}")
    for at, length in gaps[:10]:
        print(
            f"    {stamp_of(at)}  not run for {length:5.1f}s   "
            f"{freeze_verdict(watched, at, at + length)}"
        )

    slowest = sorted(ticks, key=lambda row: row.elapsed_ms, reverse=True)[:3]
    for row in slowest:
        if row.elapsed_ms > 200:
            print(f"    slow: {stamp_of(row.at)}  {row.elapsed_ms:.0f} ms  {row.outcome} {row.extra}")


def report_mode(arguments: list[str]) -> int:
    """Summarise every widget's trace: what froze, for how long, and why.

    Gaps matter most. A gap is time BetterTouchTool did not run a widget, and
    the shape across widgets is the diagnosis: all of them stopping together
    is BTT, one of them stopping alone is that widget.
    """
    try:
        minutes = float(arguments[0]) if arguments else 60.0
    except ValueError:
        print("usage: --report [minutes]")
        return 2

    rows = read_trace(minutes)
    if not rows:
        print(f"No trace entries in the last {minutes:g} min.")
        print(f"  lyrics: {config.TRACE_PATH}\n  others: {config.SHELL_TRACE_PATH}")
        return 1

    span = (rows[-1].at - rows[0].at) / 60.0
    print(
        f"Trace {stamp_of(rows[0].at)} → {stamp_of(rows[-1].at)} "
        f"({span:.1f} min, {len(rows)} entries)"
    )

    watched = read_watch(minutes)
    if not watched:
        print("  (no --watch data: gaps cannot be attributed to BTT vs the widget)")

    widgets = sorted({row.widget for row in rows if row.mode == "widget"})
    for name in widgets:
        report_widget(name, [row for row in rows if row.widget == name], watched)

    helpers = [row for row in rows if row.mode in {"sample", "fetch", "refresh"}]
    if helpers:
        print("\n=== background work ===")
        for mode in sorted({row.mode for row in helpers}):
            entries = [row for row in helpers if row.mode == mode]
            slowest = max(entries, key=lambda row: row.elapsed_ms)
            print(
                f"  {mode}: {len(entries)} runs, slowest {slowest.elapsed_ms:.0f} ms "
                f"at {stamp_of(slowest.at)}   [{tally(entries)}]"
            )
            for row in entries:
                if row.outcome in {"failed", "network_error", "denied", "error"}:
                    print(f"    {stamp_of(row.at)}  {row.widget} {row.outcome}  {row.extra}")

    return 0

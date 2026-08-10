"""Argument dispatch: the modes this widget can be invoked in."""

from __future__ import annotations

import base64
import json
import signal
import subprocess
import sys
import time
from typing import Any

from . import config
from .apple_music import is_placeholder_track, read_apple_music, read_state, write_state
from .cache import lock_path, read_cache
from .debug import clear_current_cache, diagnose_current
from .diagnostics import report_mode, watch_mode
from .fetch import background_fetch, start_background_fetch
from .locking import clear_lock
from .media_remote import read_media_remote
from .metadata import track_cache_key
from .output import log_error, trace
from .render import widget_main
from .viewport import store_measured_viewport


def fetch_mode(arguments: list[str]) -> int:
    if len(arguments) != 2:
        return 2
    key, encoded_payload = arguments
    try:
        track = json.loads(
            base64.urlsafe_b64decode(encoded_payload.encode("ascii")).decode("utf-8")
        )

        def stop_fetch(_signum: int, _frame: Any) -> None:
            raise TimeoutError(
                f"Lyrics fetch exceeded {config.FETCH_TIMEOUT_SECONDS:g}s"
            )

        signal.signal(signal.SIGALRM, stop_fetch)
        signal.setitimer(signal.ITIMER_REAL, max(config.FETCH_TIMEOUT_SECONDS, 1.0))
        try:
            background_fetch(key, track)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
        return 0
    except Exception as exc:
        log_error(f"Background fetch process failed: {exc}")
        try:
            lock_path(key).unlink()
        except OSError:
            pass
        return 1


def measure_mode(arguments: list[str]) -> int:
    """Measure the Now Playing widget's text and store the lyric's share.

    Spawned detached by the widget path when the track changes, because the
    measurement costs an osascript launch -- a fifth of a widget tick.
    """
    if len(arguments) != 2:
        return 2
    key, encoded_payload = arguments
    started = time.monotonic()
    try:
        track = json.loads(
            base64.urlsafe_b64decode(encoded_payload.encode("ascii")).decode("utf-8")
        )
        store_measured_viewport(key, track)
        return 0
    except Exception as exc:
        # The claim the widget path already wrote carries the previous
        # track's width forward, so a failure here costs accuracy for one
        # track rather than leaving the widget without a width at all.
        log_error(f"Viewport measurement failed: {exc}")
        trace("viewport", started, "failed", reason=str(exc).split(":")[0][:40])
        return 1


def request_widget_refresh(uuid: str) -> None:
    """Ask BTT to repaint the widget now, instead of at its next tick."""
    if not uuid:
        return
    try:
        subprocess.run(
            [
                "/usr/bin/osascript",
                "-e",
                f'tell application "BetterTouchTool" to refresh_widget "{uuid}"',
            ],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log_error(f"Could not refresh widget {uuid}: {exc}")


def track_changed_mode(arguments: list[str]) -> int:
    """Follow a next/previous-track command until Music settles on the track.

    A swipe sends a media key, which is asynchronous: for a moment afterwards
    Music still reports the old track, then often a placeholder, then the new
    one. So this follows Music until the title actually changes rather than
    sampling once and catching the wrong track.

    It is worth being precise about what this buys, because it is less than it
    looks. Measured, the ordinary half-second sampler already notices a swipe
    within 0.25-0.7s, so this does not make the new track known much sooner.
    What it adds is the repaint -- the widget would otherwise sit on the old
    frame until BTT's next tick, up to a second later -- and starting the
    lyrics fetch that much earlier. A song whose lyrics are not cached still
    shows its title for a second or two while the fetch runs; no amount of
    prompting here removes that.

    Always run detached -- see actions/track-changed.sh. BetterTouchTool runs
    shell actions on the same single XPC service as the widgets, so blocking
    here for a second would freeze the whole Touch Bar.
    """
    started = time.monotonic()
    uuid = arguments[0] if arguments else config.LYRICS_WIDGET_UUID
    previous_track, _ = read_state()
    previous_title = (previous_track or {}).get("title", "")

    deadline = time.monotonic() + config.TRACK_FOLLOW_SECONDS
    settled: dict[str, Any] | None = None

    while time.monotonic() < deadline:
        try:
            track = read_apple_music()
        except Exception as exc:
            log_error(f"Track-change sample failed: {exc}")
            time.sleep(config.TRACK_FOLLOW_INTERVAL)
            continue

        write_state(track)
        title = track.get("title", "")
        if title and title != previous_title and not is_placeholder_track(track):
            settled = track
            break

        # Repaint as we go: the position and the paused/playing marker are
        # already worth updating even before the new title lands.
        time.sleep(config.TRACK_FOLLOW_INTERVAL)

    if settled is not None:
        key = track_cache_key(settled)
        if read_cache(key) is None:
            try:
                start_background_fetch(key, settled)
            except Exception as exc:
                log_error(f"Could not pre-warm lyrics for the new track: {exc}")

    request_widget_refresh(uuid)
    trace(
        "track_change",
        started,
        "settled" if settled is not None else "timeout",
        title=(settled or {}).get("title", "-")[:30],
    )
    return 0


def _media_remote_fallback() -> dict[str, Any] | None:
    """A system-wide Now Playing track, if one is actually playing or paused.

    Used when Apple Music has nothing of its own to report, so QQ Music and
    other non-scriptable players still show up.
    """
    try:
        remote = read_media_remote()
    except Exception as exc:
        log_error(f"Now Playing sample failed: {exc}")
        return None
    return remote if remote.get("state") in {"playing", "paused"} else None


def sample_mode() -> int:
    """Refresh the sample the widget renders from.

    Apple Music is checked first, since it is read directly and gives an
    exact position. When it has nothing playing -- including when BTT is not
    authorized to ask it -- the system-wide Now Playing info is checked next.
    """
    started = time.monotonic()
    try:
        try:
            track = read_apple_music()
        except PermissionError:
            # The widget can still show QQ Music (or anything else) even
            # when Apple Music itself is not authorized. Only fall through
            # to the "denied" state, which surfaces the automation prompt,
            # when nothing else has an answer either.
            remote = _media_remote_fallback()
            if remote is None:
                write_state({"state": "denied"})
                trace("sample", started, "denied")
                return 1
            write_state(remote)
            trace("sample", started, remote.get("state", "?"), source="media_remote")
            return 0
        except Exception as exc:
            # Leave the previous sample in place. Apple Music briefly
            # refuses to answer while a track changes, and rendering a
            # slightly old sample beats blanking the widget for the length
            # of the hiccup.
            log_error(f"Apple Music sample failed: {exc}")
            # A slow or failing sampler is what makes the widget hold a
            # stale frame, so the reason belongs next to the widget's own
            # timings.
            trace("sample", started, "failed", reason=str(exc).split(":")[0][:40])
            return 1

        source = "apple_music"
        if track.get("state") in {"not_running", "stopped", "paused"}:
            remote = _media_remote_fallback()
            # A paused Music track must not blank the lyric while another
            # player (QQ Music, a browser) is actually playing: follow
            # MediaRemote whenever it reports playback. When it is paused
            # too -- or silent -- Music's own paused state stays, which is
            # the same blank either way, without churning the source.
            if remote is not None and (
                track.get("state") != "paused" or remote.get("state") == "playing"
            ):
                track, source = remote, "media_remote"
            elif remote is None:
                previous_track, previous_age = read_state()
                if (
                    previous_track is not None
                    and previous_track.get("state") in {"playing", "paused"}
                    and previous_age <= config.STATE_MAX_AGE_SECONDS
                ):
                    trace(
                        "sample",
                        started,
                        "held",
                        source=source,
                        reported=track.get("state", "?"),
                    )
                    return 0

        write_state(track)
        trace("sample", started, track.get("state", "?"), source=source)
        return 0
    finally:
        clear_lock(config.SAMPLER_LOCK_PATH)


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--fetch":
        return fetch_mode(sys.argv[2:])
    if len(sys.argv) >= 2 and sys.argv[1] == "--sample":
        return sample_mode()
    if len(sys.argv) >= 2 and sys.argv[1] == "--report":
        return report_mode(sys.argv[2:])
    if len(sys.argv) >= 2 and sys.argv[1] == "--watch":
        return watch_mode(sys.argv[2:])
    if len(sys.argv) >= 2 and sys.argv[1] == "--measure":
        return measure_mode(sys.argv[2:])
    if len(sys.argv) >= 2 and sys.argv[1] == "--track-changed":
        return track_changed_mode(sys.argv[2:])
    if len(sys.argv) >= 2 and sys.argv[1] == "--diagnose":
        return diagnose_current()
    if len(sys.argv) >= 2 and sys.argv[1] == "--clear-current":
        return clear_current_cache()
    return widget_main()

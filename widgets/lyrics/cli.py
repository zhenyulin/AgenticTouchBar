"""Argument dispatch: the modes this widget can be invoked in.

Only what every mode needs is imported here. The rest is imported by the mode
that uses it, because the mode that runs by far the most often -- the widget
tick, every second, on the one script-runner service every BetterTouchTool
widget shares -- needs almost none of it. Importing the diagnostics, the
fetch pipeline and its six provider modules (which between them pull in
sqlite3 and xml.etree) up here cost that tick ~40 ms to load code it never
called.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from . import config
from .runtime.output import log_error, trace


def fetch_mode(arguments: list[str]) -> int:
    import base64
    import signal

    from .fetch import background_fetch
    from .runtime.cache import lock_path

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


def request_widget_refresh(uuid: str) -> None:
    """Ask BTT to repaint the widget now, instead of at its next tick."""
    import subprocess

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


def _track_identity(track: dict[str, Any] | None) -> str:
    """The identity two samples are compared on: title + artist + album."""
    if not track:
        return ""
    return "\t".join(str(track.get(key) or "") for key in ("title", "artist", "album"))


def _visible_track(track: dict[str, Any] | None) -> bool:
    """A track the row is actually showing: playing or paused, with a title."""
    return (
        bool(track)
        and track.get("state") in {"playing", "paused"}
        and bool(track.get("title"))
    )


def _btt_call(*statements: str) -> None:
    """AppleScript for BetterTouchTool, detached and forgettable.

    Several statements go to one osascript rather than one each, because
    their order is the point: the lyric has to leave the screen before the
    row beside it is hidden or repainted, and two detached launches a
    millisecond apart are ordered by nothing. Inside one process they run in
    the order given. It is also one ~200 ms launch instead of two, on the
    single script-runner service every widget shares.
    """
    import subprocess

    script = "\n".join(('tell application "BetterTouchTool"', *statements, "end tell"))
    try:
        subprocess.Popen(
            ["/usr/bin/osascript", "-e", script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


def _clear_lyric_then_row(hide_row: bool) -> None:
    """Clear the Lyrics widget, then settle the Now Playing row -- in that order.

    The row is hidden when the track is gone for good, otherwise repainted at
    once. Its text is what sets the pair's width, so leaving it to redraw
    whenever its next tick happens to fall is what made a transition flicker;
    and a lyric outliving the row it belongs beside is the wrong order in the
    more visible direction -- the lyric kept rolling for seconds after the
    row it annotates had disappeared.
    """
    statements = []
    if config.LYRICS_WIDGET_UUID:
        statements.append(
            f'update_touch_bar_widget "{config.LYRICS_WIDGET_UUID}" text ""'
        )
    if config.NOW_PLAYING_WIDGET_UUID:
        # BetterTouchTool drops a script widget whose text is empty, which is
        # how the row is hidden; a refresh instead makes it redraw now, while
        # the lyric beside it is already clear.
        statements.append(
            f'update_touch_bar_widget "{config.NOW_PLAYING_WIDGET_UUID}" text ""'
            if hide_row
            else f'refresh_widget "{config.NOW_PLAYING_WIDGET_UUID}"'
        )
    if statements:
        _btt_call(*statements)


def clear_marker() -> None:
    """Drop the closing marker; the widget renders samples normally again."""
    try:
        config.CLEARED_MARKER_PATH.unlink()
    except OSError:
        pass


def closing_sequence(previous: dict[str, Any], hide_row: bool) -> None:
    """The closing sequence: clear the Lyrics widget, then settle the row.

    Runs in the event watcher (the sampler, or the track-change follow)
    before the state write that ends the on-screen track, so the old lyric
    never lingers after the row beside it changes or disappears -- see
    _clear_lyric_then_row for where the "clear first, then settle the row"
    order is enforced. widgets/now-playing.sh runs the same sequence when its
    own tick is the one that notices first.

    The marker records what was cleared: the Lyrics widget holds its frame
    empty while its state sample still matches that identity (render.py
    cleared_while_sample_current), and the next sample that moves past it --
    the new track, or the stopped state -- drops the hold.
    """
    from .runtime.cache import atomic_write_json

    marker = {"at": time.time()}
    for key in ("title", "artist", "album"):
        marker[key] = str(previous.get(key) or "")
    try:
        atomic_write_json(config.CLEARED_MARKER_PATH, marker)
    except OSError:
        pass

    _clear_lyric_then_row(hide_row)


def maybe_transition_sequence(
    previous: dict[str, Any] | None, new: dict[str, Any]
) -> None:
    """Run whichever sequence this transition calls for, before the state write.

    A closing transition is a visible track ending (stopped, or the app
    quit) or the track identity changing. The Now Playing row is hidden
    explicitly only when the app is gone: on a plain stop or pause the row
    keeps showing the track by design (HideWhenPaused: 0), and on a track
    change it shows the new track itself.

    A pause is not a closing transition -- the row keeps its track -- but the
    lyric still goes, because there is no current line to a track that is not
    moving. Both widgets would reach that on their own next tick, up to a
    second apart and in either order, so the pair is driven here too: the
    lyric out first, then the row repainted in its paused shade.
    """
    state = (new or {}).get("state", "")
    if _visible_track(previous) and state in {"stopped", "not_running"}:
        closing_sequence(previous, hide_row=(state == "not_running"))
    elif (
        _visible_track(previous)
        and _visible_track(new)
        and _track_identity(previous) != _track_identity(new)
    ):
        closing_sequence(previous, hide_row=False)
    elif (previous or {}).get("state") == "playing" and state == "paused":
        # No cleared-marker here, unlike closing_sequence: a paused track
        # keeps its identity, so a marker naming it would still be holding
        # the widget empty when playback resumes. The widget's own next tick
        # renders the paused state as an empty frame anyway, which is what
        # this is only bringing forward.
        _clear_lyric_then_row(hide_row=False)
    else:
        # The transition is over, or never happened: drop a leftover marker
        # (e.g. a watcher that died mid-sequence) once the sample moves past
        # the identity it cleared.
        try:
            marker = json.loads(config.CLEARED_MARKER_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if _track_identity(new) != _track_identity(marker):
            clear_marker()


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
    from .fetch import start_background_fetch
    from .runtime.cache import read_cache
    from .sources.apple_music import (
        is_placeholder_track,
        read_apple_music,
        read_state,
        write_state,
    )
    from .text.metadata import track_cache_key

    started = time.monotonic()
    uuid = arguments[0] if arguments else config.LYRICS_WIDGET_UUID
    previous_track, _ = read_state()
    previous_title = (previous_track or {}).get("title", "")

    # The swipe makes the on-screen lyric stale the moment the track turns
    # over, so run the closing sequence first: clear the Lyrics widget, and
    # leave the marker holding it clear while the follow below settles.
    if previous_track is not None and previous_track.get("title"):
        closing_sequence(previous_track, hide_row=False)

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

    if settled is None:
        # The swipe changed nothing (a no-op media key): release the hold so
        # the current lyric renders again. On a real settle the marker is
        # left in place -- it protects the state while the sampler catches
        # up with the new track, and the sampler drops it itself.
        clear_marker()
    elif config.NOW_PLAYING_WIDGET_UUID:
        # The row before the lyric: the closing sequence above ran while
        # Music still reported the old track, so this is the repaint that
        # actually shows the new one, and the lyric has been clear ever
        # since. Without it the row would sit on the old track until its
        # own next tick, up to a second later.
        _btt_call(f'refresh_widget "{config.NOW_PLAYING_WIDGET_UUID}"')

    request_widget_refresh(uuid)
    trace(
        "track_change",
        started,
        "settled" if settled is not None else "timeout",
        title=(settled or {}).get("title", "-")[:30],
    )
    return 0


def _hold_previous_sample(previous: dict[str, Any] | None, age: float) -> bool:
    """Whether to keep the previous sample when nothing reports a track.

    MediaRemote goes quiet for a beat while a player keeps playing, and a
    sample written from that silence blanks the lyric mid-song. That is the
    only reason this hold exists, so it is all it covers: a MediaRemote
    sample is held through the silence, for as long as the widget would still
    render it, and only while the app that published it is still running --
    a player that has been quit publishes exactly the same silence, and
    waiting it out is not a hold, it is a delay.

    Nothing else is held. Apple Music is read directly -- pgrep for the
    process, then AppleScript for its state -- so not_running, stopped and
    paused are answers rather than silence. Holding them kept the sampler
    blind to the transition for STATE_MAX_AGE_SECONDS, which is exactly the
    age at which widgets/now-playing.sh gives up on the same sample and hides
    the row: quitting Music took the row off screen while the lyric beside it
    played on for the rest of the hold.
    """
    if previous is None or previous.get("state") not in {"playing", "paused"}:
        return False
    if age > config.STATE_MAX_AGE_SECONDS:
        return False
    if previous.get("source") != "media_remote":
        return False

    from .sources.media_remote import player_is_running

    return player_is_running(str(previous.get("bundle_id") or ""))


def _abandoned_session(
    remote: dict[str, Any] | None, previous: dict[str, Any] | None
) -> bool:
    """Whether MediaRemote is describing a session whose player has quit.

    MediaRemote does not drop a session the moment its app goes: it keeps
    publishing the last track with isPlaying false for a second or two, which
    is indistinguishable from a pause. Measured at 1.7 s on a QQ Music quit
    (trace, 2026-08-16) -- and for that whole time the pair came apart in the
    one way each widget was individually right about: the lyric went, because
    a paused track has no current line, while the row stayed showing its
    track, because a paused row is meant to.

    Apple Music never had this problem: pgrep answers whether Music is
    running before its state is ever consulted (sources/apple_music.py), so a
    quit reads as gone in one step. This asks MediaRemote's sessions the same
    question, and only where the answer changes anything: the moment a
    playing track turns paused. A player that really is paused answers "still
    running" and keeps its row.

    A quit while already paused is not covered, deliberately -- there is no
    transition to hang the question on, nothing is on screen to come apart,
    and MediaRemote drops the session on its own within about two seconds.
    """
    if remote is None or remote.get("state") != "paused":
        return False
    if (previous or {}).get("state") != "playing":
        return False

    from .sources.media_remote import player_is_running

    return not player_is_running(str(remote.get("bundle_id") or ""))


def _media_remote_fallback() -> dict[str, Any] | None:
    """A system-wide Now Playing track, if one is actually playing or paused.

    Used when Apple Music has nothing of its own to report, so QQ Music and
    other non-scriptable players still show up.
    """
    from .sources.media_remote import read_media_remote

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
    from .runtime.locking import clear_lock
    from .sources.apple_music import read_apple_music, read_state, write_state

    started = time.monotonic()
    previous_track, previous_age = read_state()
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
            maybe_transition_sequence(previous_track, remote)
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
            if _abandoned_session(remote, previous_track):
                # A session nobody is running is not a session. Dropping it
                # here puts a MediaRemote player's quit on the same path as
                # Apple Music's: nothing left to hold, so the closing
                # sequence runs now and takes the lyric and the row together.
                remote = None
            # A paused Music track must not blank the lyric while another
            # player (QQ Music, a browser) is actually playing: follow
            # MediaRemote whenever it reports playback. When it is paused
            # too -- or silent -- Music's own paused state stays, which is
            # the same blank either way, without churning the source.
            if remote is not None and (
                track.get("state") != "paused" or remote.get("state") == "playing"
            ):
                track, source = remote, "media_remote"
            elif remote is None and _hold_previous_sample(
                previous_track, previous_age
            ):
                trace(
                    "sample",
                    started,
                    "held",
                    source=source,
                    reported=track.get("state", "?"),
                )
                return 0

        # Run for every sample, not only the paths above: a pause or a track
        # change reported by MediaRemote is the same transition for the pair
        # of widgets as one Apple Music reported, and a sample that has moved
        # past a cleared track is what drops a leftover marker.
        maybe_transition_sequence(previous_track, track)
        write_state(track)
        trace("sample", started, track.get("state", "?"), source=source)
        return 0
    finally:
        clear_lock(config.SAMPLER_LOCK_PATH)


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) >= 2 else ""

    if mode == "--fetch":
        return fetch_mode(sys.argv[2:])
    if mode == "--sample":
        return sample_mode()
    if mode == "--track-changed":
        return track_changed_mode(sys.argv[2:])
    if mode == "--report":
        from .diag.diagnostics import report_mode

        return report_mode(sys.argv[2:])
    if mode == "--watch":
        from .diag.diagnostics import watch_mode

        return watch_mode(sys.argv[2:])
    if mode == "--diagnose":
        from .diag.debug import diagnose_current

        return diagnose_current()
    if mode == "--clear-current":
        from .diag.debug import clear_current_cache

        return clear_current_cache()

    # The default, and the only mode BetterTouchTool itself runs.
    from .display.render import widget_main

    return widget_main()

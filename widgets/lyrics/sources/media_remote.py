"""Reading macOS's system-wide Now Playing info for players other than Apple
Music -- QQ Music, Spotify, browsers, anything that registers itself as the
system's Now Playing source -- via nowplaying-cli, a thin wrapper around the
private MediaRemote framework. It is the same source BetterTouchTool's native
Now Playing widget reads, so this is how the Lyrics widget follows QQ Music.

Apple Music is read directly over AppleScript instead (see apple_music.py):
that is faster and gives an exact playback position. MediaRemote does not --
QQ Music's normalized fields report an elapsed time of 0, so position here
is tracked by hand across samples: reset to zero on a title/artist change,
held while paused, advanced by wall clock while playing. The raw Now
Playing dictionary, however, does carry a real elapsed time that QQ Music
refreshes on player events (seek, pause, resume, track restarts), so the
hand-tracked clock re-anchors to it whenever the reported value changes --
an in-player seek is followed on the next sample instead of being lost.

The dictionary cannot tell pause from play, though: QQ Music pushes a
playbackRate of 1 even while paused. The compiled nowplaying-state helper
(actions/nowplaying-state.m) adds the framework's own isPlaying flag -- the
same source Control Center's Now Playing tile reads -- which is
authoritative when present; without the helper, the sampler falls back to
the playback rate and paused tracks keep scrolling.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any

from .. import config
from ..runtime.cache import atomic_write_json
from ..runtime.output import log_error

_CLI_PATH: str | None = ""  # "" means not resolved yet; None means missing.
# Which players were found running, per process -- one sampler run.
_RUNNING_PLAYERS: dict[str, bool] = {}
_STATE_BIN_PATH: str | None = ""  # "" means not resolved yet; None means missing.


def _cli_path() -> str | None:
    """nowplaying-cli's path, or None.

    Walks PATH rather than calling shutil.which: importing shutil drags in the
    archive registries (lzma, bz2, zstd) for ~15 ms, on a sampler that is a
    fresh process twice a second.
    """
    global _CLI_PATH
    if _CLI_PATH == "":
        _CLI_PATH = None
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            candidate = os.path.join(directory, "nowplaying-cli")
            if os.access(candidate, os.X_OK) and not os.path.isdir(candidate):
                _CLI_PATH = candidate
                break
    return _CLI_PATH


def _state_bin() -> str | None:
    """The nowplaying-state helper, or None when it isn't installed.

    The helper reports the true play/pause state (see actions/nowplaying-state.m
    for why the info dictionary alone cannot). Its absence means degraded pause
    detection, so it is logged at most hourly -- the sampler is a fresh process
    every second, so an unconditional log would spam error.log.
    """
    global _STATE_BIN_PATH
    if _STATE_BIN_PATH == "":
        path = config.NOWPLAYING_STATE_BIN
        if path and os.access(path, os.X_OK):
            _STATE_BIN_PATH = path
        else:
            _STATE_BIN_PATH = None
            log_path = config.CACHE_DIR / "error.log"
            try:
                recent = (
                    log_path.exists() and time.time() - log_path.stat().st_mtime < 3600
                )
            except OSError:
                recent = False
            if not recent:
                log_error(
                    f"nowplaying-state helper missing at {path}; "
                    "paused QQ Music tracks will keep scrolling"
                )
    return _STATE_BIN_PATH


_RAW_FIELD_KEYS = {
    "kMRMediaRemoteNowPlayingInfoTitle": "title",
    "kMRMediaRemoteNowPlayingInfoArtist": "artist",
    "kMRMediaRemoteNowPlayingInfoAlbum": "album",
    "kMRMediaRemoteNowPlayingInfoDuration": "duration",
    "kMRMediaRemoteNowPlayingInfoPlaybackRate": "playbackRate",
    "kMRMediaRemoteNowPlayingInfoElapsedTime": "elapsedTime",
    "kMRMediaRemoteNowPlayingInfoClientBundleIdentifier": "clientBundleIdentifier",
}


def _run_json(command: list[str]) -> dict[str, Any] | None:
    """One bounded MediaRemote query, decoded."""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=config.MEDIA_REMOTE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("MediaRemote query timed out") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not run MediaRemote query: {exc}") from exc

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "MediaRemote query failed")

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Unexpected MediaRemote output: {result.stdout!r}") from exc
    return payload if isinstance(payload, dict) else None


def _shared_raw() -> dict[str, Any] | None:
    """The dictionary widgets/now-playing.sh wrote on its own tick, if fresh.

    That widget runs every second and already pays for nowplaying-cli, so a
    sampler running it again a moment later spends ~0.22 s to learn the same
    thing. Anything older than the max age is ignored: the widget is hidden
    while nothing allowed is playing, and a dictionary from minutes ago would
    resurrect a finished track.
    """
    path = config.MEDIA_REMOTE_RAW_PATH
    try:
        if time.time() - path.stat().st_mtime > config.MEDIA_REMOTE_RAW_MAX_AGE_SECONDS:
            return None
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_raw() -> dict[str, Any] | None:
    """The raw MediaRemote Now Playing dictionary, mapped to plain keys.

    Two sources, because neither answers the whole question:

    The dictionary comes from nowplaying-cli -- preferably the copy
    widgets/now-playing.sh already wrote (see _shared_raw). It is the only
    view where QQ Music reports its elapsed time (the normalized ``get``
    command always shows 0), and the only one carrying the session holder's
    bundle id, which read_media_remote's allowlist needs.

    ``isPlaying`` comes from the compiled nowplaying-state helper, which is
    the only source for it; without the helper, pause detection degrades to
    the playback rate and paused tracks keep scrolling.

    The helper cannot stand in for the dictionary: it drops every NSData
    value, and the bundle id arrives inside one. A sampler that read the
    holder's id from the helper alone always saw an empty string, so the
    allowlist rejected every track and the MediaRemote fallback -- QQ Music
    and anything else non-scriptable -- never rendered at all.
    """
    payload = _shared_raw()
    if payload is None and (cli := _cli_path()) is not None:
        payload = _run_json([cli, "get-raw"])
    if payload is None:
        return None

    mapped = {
        name: payload[key] for key, name in _RAW_FIELD_KEYS.items() if key in payload
    }

    if (state_bin := _state_bin()) is not None:
        state = _run_json([state_bin]) or {}
        is_playing = state.get("isPlaying")
        if is_playing is not None:
            mapped["isPlaying"] = bool(is_playing)
        # The helper's own dictionary is the more recent reading, and it
        # carries the elapsed time the shared copy may be a second behind on.
        # It is only trusted for the track it describes.
        if str(state.get("kMRMediaRemoteNowPlayingInfoTitle") or "").strip() == str(
            mapped.get("title") or ""
        ).strip():
            for key, name in _RAW_FIELD_KEYS.items():
                if key in state:
                    mapped[name] = state[key]

    return mapped or None


def player_is_running(bundle_id: str) -> bool:
    """Whether the app that published a MediaRemote sample is still running.

    MediaRemote goes quiet for a beat while a player keeps playing, and the
    sampler holds the previous sample through that silence rather than blank
    the lyric mid-song (cli._hold_previous_sample). A player that has been
    quit publishes the same silence -- and holding on that is what left the
    lyric rolling for seconds after the Now Playing row beside it had gone.

    Launch Services tells the two apart in ~10 ms: it prints an ASN line for a
    running app and nothing for one that is gone, exit code 0 either way. It
    is what pgrep is to Apple Music (sources/apple_music.py), for a player
    whose process name nothing here knows.

    True whenever the answer is unavailable -- no bundle id, lsappinfo
    missing or slow: an unknown state must not tear the pair down mid-song.

    Memoized for the life of the process, which is one sampler run: the
    question is asked from two places in the same transition and the answer
    cannot change between them.
    """
    if not bundle_id:
        return True
    if bundle_id in _RUNNING_PLAYERS:
        return _RUNNING_PLAYERS[bundle_id]
    try:
        result = subprocess.run(
            ["/usr/bin/lsappinfo", "find", f"bundleID={bundle_id}"],
            capture_output=True,
            text=True,
            timeout=config.MEDIA_REMOTE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    if result.returncode != 0:
        return True
    _RUNNING_PLAYERS[bundle_id] = bool(result.stdout.strip())
    return _RUNNING_PLAYERS[bundle_id]


def _read_position_state() -> dict[str, Any]:
    try:
        with config.MEDIA_REMOTE_POSITION_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _track_identity(title: str, artist: str) -> str:
    return f"{title}\x1f{artist}"


def read_media_remote() -> dict[str, Any]:
    """The system-wide Now Playing track, when an allowed player holds it.

    Returns {"state": "not_running"} when nothing is playing anywhere, or
    when the current holder is not on config.MEDIA_REMOTE_ALLOWED_BUNDLE_IDS
    -- a browser playing a YouTube tab included. The allowlist mirrors
    widgets/now-playing.sh, so the same holders drive both widgets.
    """
    if not config.ENABLE_MEDIA_REMOTE:
        return {"state": "not_running"}

    raw = _read_raw()
    if not raw:
        return {"state": "not_running"}

    title = str(raw.get("title") or "").strip()
    bundle_id = raw.get("clientBundleIdentifier") or ""
    # Case-folded, like now-playing.sh (QQ Music registers
    # com.tencent.QQMusicMac).
    if not title or bundle_id.lower() not in config.MEDIA_REMOTE_ALLOWED_BUNDLE_IDS:
        return {"state": "not_running"}

    artist = str(raw.get("artist") or "").strip()
    album = str(raw.get("album") or "").strip()
    try:
        duration = max(float(raw.get("duration") or 0.0), 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    try:
        rate = float(raw.get("playbackRate") or 0.0)
    except (TypeError, ValueError):
        rate = 0.0
    # QQ Music pushes playbackRate 1 even while paused, so the rate cannot
    # be trusted. The helper's isPlaying comes from the framework's own
    # now-playing state -- the same source Control Center's tile reads --
    # and is authoritative when present; without the helper, fall back to
    # the rate.
    is_playing = raw.get("isPlaying")
    playing = is_playing if is_playing is not None else rate > 0

    now = time.time()
    identity = _track_identity(title, artist)
    saved = _read_position_state()
    previous_elapsed = 0.0
    position = 0.0
    if saved.get("identity") == identity:
        previous_elapsed = max(float(saved.get("elapsed_time", 0.0) or 0.0), 0.0)
        position = max(float(saved.get("position", 0.0) or 0.0), 0.0)
        if playing:
            elapsed = max(now - float(saved.get("updated_at", now) or now), 0.0)
            position += elapsed * rate
    try:
        reported = max(float(raw.get("elapsedTime") or 0.0), 0.0)
    except (TypeError, ValueError):
        reported = 0.0
    # QQ Music freezes the elapsed-time field during playback and only
    # refreshes it on player events (seek, pause, resume, track restarts),
    # so the hand-tracked clock drifts from reality after a seek. A change
    # in the reported value is that event signal: re-anchor the clock to
    # it. Players that report live elapsed times re-anchor every sample,
    # which keeps them exact too.
    if reported > 0 and abs(reported - previous_elapsed) >= 0.5:
        position = reported
    if duration:
        position = min(position, duration)
    position = max(position, 0.0)

    atomic_write_json(
        config.MEDIA_REMOTE_POSITION_PATH,
        {
            "identity": identity,
            "position": position,
            "updated_at": now,
            "elapsed_time": reported,
        },
    )

    return {
        "state": "playing" if playing else "paused",
        "title": title,
        "artist": artist,
        "album": album,
        "duration": duration,
        "position": position,
        # Which player this sample came from; widgets/now-playing.sh reads
        # it for its allowlist fallback (media_remote samples are rejected
        # there -- they come from the holder the allowlist already refused).
        "source": "media_remote",
        # Who published it, so a later sampler run can ask whether that app
        # is still running when MediaRemote goes quiet -- see
        # player_is_running above.
        "bundle_id": bundle_id,
    }

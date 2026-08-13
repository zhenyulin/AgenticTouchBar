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
import shutil
import subprocess
import time
from typing import Any

from .. import config
from ..runtime.cache import atomic_write_json
from ..runtime.output import log_error

_CLI_PATH: str | None = ""  # "" means not resolved yet; None means missing.
_STATE_BIN_PATH: str | None = ""  # "" means not resolved yet; None means missing.


def _cli_path() -> str | None:
    global _CLI_PATH
    if _CLI_PATH == "":
        _CLI_PATH = shutil.which("nowplaying-cli")
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


def _read_raw() -> dict[str, Any] | None:
    """The raw MediaRemote Now Playing dictionary, mapped to plain keys.

    The compiled nowplaying-state helper is preferred: it returns the same
    raw framework dictionary (the only view where QQ Music reports its
    elapsed time -- the normalized ``get`` command always shows 0 for it)
    plus an ``isPlaying`` flag from the framework's own playback state.
    Without the helper, ``nowplaying-cli get-raw`` supplies the dictionary
    alone and pause detection degrades to the playback rate.
    """
    if (state_bin := _state_bin()) is not None:
        command = [state_bin]
    elif (cli := _cli_path()) is not None:
        command = [cli, "get-raw"]
    else:
        return None

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
    if not isinstance(payload, dict):
        return None
    mapped = {
        name: payload[key] for key, name in _RAW_FIELD_KEYS.items() if key in payload
    }
    is_playing = payload.get("isPlaying")
    if is_playing is not None:
        mapped["isPlaying"] = bool(is_playing)
    return mapped


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
    }

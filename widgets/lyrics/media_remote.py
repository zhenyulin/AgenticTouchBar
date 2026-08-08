"""Reading macOS's system-wide Now Playing info for players other than Apple
Music -- QQ Music, Spotify, browsers, anything that registers itself as the
system's Now Playing source -- via nowplaying-cli, a thin wrapper around the
private MediaRemote framework. It is the same source BetterTouchTool's native
Now Playing widget reads, so this is how the Lyrics widget follows QQ Music.

Apple Music is read directly over AppleScript instead (see apple_music.py):
that is faster and gives an exact playback position. MediaRemote does not --
QQ Music, at least, never populates its elapsed-time field, which stays at 0
for a track's entire run -- so position here is tracked by hand across
samples: reset to zero on a title/artist change, held while paused, advanced
by wall clock while playing. It will not follow a seek made inside the
player itself, only play/pause and track changes.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from typing import Any

from . import config
from .cache import atomic_write_json

_CLI_PATH: str | None = ""  # "" means not resolved yet; None means missing.


def _cli_path() -> str | None:
    global _CLI_PATH
    if _CLI_PATH == "":
        _CLI_PATH = shutil.which("nowplaying-cli")
    return _CLI_PATH


def _read_raw() -> dict[str, Any] | None:
    cli = _cli_path()
    if cli is None:
        return None

    try:
        result = subprocess.run(
            [
                cli,
                "get",
                "--json",
                "title",
                "artist",
                "album",
                "duration",
                "playbackRate",
                "clientBundleIdentifier",
            ],
            capture_output=True,
            text=True,
            timeout=config.MEDIA_REMOTE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("nowplaying-cli query timed out") from exc
    except OSError as exc:
        raise RuntimeError(f"Could not run nowplaying-cli: {exc}") from exc

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "nowplaying-cli failed")

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Unexpected nowplaying-cli output: {result.stdout!r}") from exc
    return payload if isinstance(payload, dict) else None


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
    """The system-wide Now Playing track, from whichever app currently holds it.

    Returns {"state": "not_running"} when nothing is playing anywhere, or
    when the current holder is Apple Music itself -- see
    MEDIA_REMOTE_IGNORED_BUNDLE_IDS.
    """
    if not config.ENABLE_MEDIA_REMOTE:
        return {"state": "not_running"}

    raw = _read_raw()
    if not raw:
        return {"state": "not_running"}

    title = str(raw.get("title") or "").strip()
    bundle_id = raw.get("clientBundleIdentifier") or ""
    if not title or bundle_id in config.MEDIA_REMOTE_IGNORED_BUNDLE_IDS:
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
    playing = rate > 0

    now = time.time()
    identity = _track_identity(title, artist)
    saved = _read_position_state()
    if saved.get("identity") == identity:
        position = float(saved.get("position", 0.0) or 0.0)
        if playing:
            elapsed = max(now - float(saved.get("updated_at", now) or now), 0.0)
            position += elapsed * rate
    else:
        position = 0.0
    if duration:
        position = min(position, duration)
    position = max(position, 0.0)

    atomic_write_json(
        config.MEDIA_REMOTE_POSITION_PATH,
        {"identity": identity, "position": position, "updated_at": now},
    )

    return {
        "state": "playing" if playing else "paused",
        "title": title,
        "artist": artist,
        "album": album,
        "duration": duration,
        "position": position,
    }

"""Reading Apple Music's state, and the sampled-state cache the widget reads.

The widget never queries Apple Music itself, not even as a fallback. An
osascript round trip costs a few hundred milliseconds and stalls for seconds
while a track changes -- and a fallback fires precisely when Music is being
slow, so every tick would pay that cost, at a one second interval, on the
single XPC service BetterTouchTool runs all widget scripts through. That
starves this widget and swallows every other widget's tap-refresh with it.

So sampling only ever happens in the detached helper (see current_track),
and a tick that finds nothing fresh reprints the last frame and waits for
the next sample.
"""

from __future__ import annotations

import json
import subprocess
import time
from typing import Any

from . import config
from .cache import atomic_write_json
from .fetch import start_sampler
from .output import log_error


def music_is_running() -> bool:
    try:
        result = subprocess.run(
            ["/usr/bin/pgrep", "-x", "Music"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=0.25,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def read_apple_music() -> dict[str, Any]:
    if not music_is_running():
        return {"state": "not_running"}

    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", config.APPLE_MUSIC_SCRIPT],
            capture_output=True,
            text=True,
            timeout=config.APPLE_MUSIC_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Apple Music query timed out") from exc

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "-1743" in stderr or "not authorized" in stderr.lower():
            raise PermissionError(
                "Allow BetterTouchTool to control Music in System Settings → Privacy & Security → Automation"
            )
        raise RuntimeError(stderr or "osascript could not read Apple Music")

    raw = result.stdout.strip()
    if raw in {"stopped", ""}:
        return {"state": "stopped"}

    fields = raw.split(config.UNIT_SEPARATOR)
    if len(fields) != 7:
        raise RuntimeError(f"Unexpected Apple Music response: {raw!r}")

    state, title, artist, genre, album, duration, position = fields
    try:
        duration_value = float(duration)
    except ValueError:
        duration_value = 0.0
    try:
        position_value = float(position)
    except ValueError:
        position_value = 0.0

    track = {
        "state": state,
        "title": title.strip(),
        "artist": artist.strip(),
        "genre": genre.strip(),
        "album": album.strip(),
        "duration": max(duration_value, 0.0),
        "position": max(position_value, 0.0),
    }
    return track


def write_state(track: dict[str, Any]) -> None:
    atomic_write_json(config.STATE_PATH, {"sampled_at": time.time(), "track": track})


def read_state() -> tuple[dict[str, Any] | None, float]:
    """The newest Apple Music sample and how many seconds old it is."""
    try:
        with config.STATE_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return None, float("inf")
    except (OSError, json.JSONDecodeError):
        return None, float("inf")

    track = payload.get("track")
    if not isinstance(track, dict):
        return None, float("inf")
    try:
        age = time.time() - float(payload.get("sampled_at", 0.0))
    except (TypeError, ValueError):
        return None, float("inf")
    return track, max(age, 0.0)


def project_playback(track: dict[str, Any], age: float) -> dict[str, Any]:
    """Carry a sample's playback position forward by the sample's own age.

    A sample is always slightly stale, so this is not just a workaround for
    reading Apple Music off the widget's path: advancing the position by the
    wall clock puts the lyric closer to the music than the raw sample does.
    """
    if track.get("state") != "playing" or age <= 0:
        return track

    position = float(track.get("position", 0.0) or 0.0) + age
    duration = float(track.get("duration", 0.0) or 0.0)
    if duration:
        position = min(position, duration)

    projected = dict(track)
    projected["position"] = position
    return projected


# How stale the sample this tick rendered from was. Kept for the trace, so
# that recording it costs nothing rather than a second read of the state.
_SAMPLE_AGE: float = float("inf")


def current_track() -> dict[str, Any] | None:
    """The newest Apple Music sample, or None when there is not a usable one.

    Recovery costs nothing: a sampler is asked for on every tick.
    """
    global _SAMPLE_AGE

    track, age = read_state()
    _SAMPLE_AGE = age

    if age >= config.STATE_REFRESH_SECONDS:
        try:
            start_sampler()
        except Exception as exc:
            log_error(f"Could not start Apple Music sampler: {exc}")

    if track is not None and age <= config.STATE_MAX_AGE_SECONDS:
        return project_playback(track, age)
    return None


def _last_sample_age_ms() -> str:
    if _SAMPLE_AGE == float("inf"):
        return "none"
    return f"{_SAMPLE_AGE * 1000:.0f}"


def is_placeholder_track(track: dict[str, Any]) -> bool:
    title = track.get("title", "").strip()
    if track.get("artist", "").strip():
        return False
    return title.casefold().rstrip(".…") in config.PLACEHOLDER_TITLES

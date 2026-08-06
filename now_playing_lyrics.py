#!/usr/bin/env python3
"""BetterTouchTool Touch Bar lyrics widget for Apple Music.

Apple Music supplies track metadata and playback position through AppleScript.
LRCLIB supplies synchronized LRC lyrics, which are cached locally.

The normal widget path never blocks on the network: a cache miss starts a
background fetch and immediately returns a status string to BetterTouchTool.
"""

from __future__ import annotations

import base64
import bisect
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# ---- User-tunable defaults -------------------------------------------------
VIEWPORT_WIDTH = int(os.environ.get("BTT_LYRICS_WIDTH", "42"))
SYNC_OFFSET_SECONDS = float(os.environ.get("BTT_LYRICS_OFFSET", "0.0"))
SCROLL_LONG_LINES = os.environ.get("BTT_LYRICS_SCROLL", "1") not in {"0", "false", "False"}
SCROLL_DELAY_SECONDS = float(os.environ.get("BTT_LYRICS_SCROLL_DELAY", "0.8"))
SCROLL_CELLS_PER_SECOND = float(os.environ.get("BTT_LYRICS_SCROLL_RATE", "5.0"))
NETWORK_TIMEOUT_SECONDS = float(os.environ.get("BTT_LYRICS_NETWORK_TIMEOUT", "5.0"))

CACHE_DIR = Path.home() / "Library" / "Caches" / "BTTNowPlayingLyrics"
API_BASE = "https://lrclib.net/api"
USER_AGENT = "BTT-NowPlaying-Lyrics/1.0 (personal macOS Touch Bar widget)"
LOCK_MAX_AGE_SECONDS = 30
RETRY_NETWORK_ERROR_SECONDS = 300
RETRY_NOT_FOUND_SECONDS = 24 * 60 * 60

UNIT_SEPARATOR = "\x1f"
TIMESTAMP_RE = re.compile(r"\[(\d{1,3}):(\d{2}(?:\.\d{1,3})?)\]")
ENHANCED_TIMESTAMP_RE = re.compile(r"<\d{1,3}:\d{2}(?:\.\d{1,3})?>")
OFFSET_RE = re.compile(r"\[offset:([+-]?\d+)\]", re.IGNORECASE)

APPLE_MUSIC_SCRIPT = r'''
tell application "Music"
    set currentState to (player state as text)
    if currentState is "stopped" then return currentState

    set currentTrack to current track
    set sep to ASCII character 31

    try
        set trackName to (name of currentTrack) as text
    on error
        set trackName to ""
    end try

    try
        set artistName to (artist of currentTrack) as text
    on error
        set artistName to ""
    end try

    try
        set albumName to (album of currentTrack) as text
    on error
        set albumName to ""
    end try

    try
        set trackDuration to (duration of currentTrack) as text
    on error
        set trackDuration to "0"
    end try

    try
        set currentPosition to (player position) as text
    on error
        set currentPosition to "0"
    end try

    return currentState & sep & trackName & sep & artistName & sep & albumName & sep & trackDuration & sep & currentPosition
end tell
'''


def emit(text: str) -> None:
    """Print exactly one compact line for BetterTouchTool."""
    text = " ".join(str(text).replace("\r", " ").replace("\n", " ").split())
    print(text or "♪")


def log_error(message: str) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with (CACHE_DIR / "error.log").open("a", encoding="utf-8") as handle:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            handle.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


def music_is_running() -> bool:
    try:
        result = subprocess.run(
            ["/usr/bin/pgrep", "-x", "Music"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1,
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
            ["/usr/bin/osascript", "-e", APPLE_MUSIC_SCRIPT],
            capture_output=True,
            text=True,
            timeout=3,
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

    fields = raw.split(UNIT_SEPARATOR)
    if len(fields) != 6:
        raise RuntimeError(f"Unexpected Apple Music response: {raw!r}")

    state, title, artist, album, duration, position = fields
    try:
        duration_value = float(duration)
    except ValueError:
        duration_value = 0.0
    try:
        position_value = float(position)
    except ValueError:
        position_value = 0.0

    return {
        "state": state,
        "title": title.strip(),
        "artist": artist.strip(),
        "album": album.strip(),
        "duration": max(duration_value, 0.0),
        "position": max(position_value, 0.0),
    }


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"\b(?:feat|ft)\.?\s+.*$", "", value)
    value = re.sub(r"\((?:[^)]*remaster[^)]*|[^)]*version[^)]*)\)", "", value)
    value = re.sub(r"\[(?:[^]]*remaster[^]]*|[^]]*version[^]]*)\]", "", value)
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def similarity(left: str, right: str) -> float:
    return difflib.SequenceMatcher(None, normalized(left), normalized(right)).ratio()


def track_cache_key(track: dict[str, Any]) -> str:
    identity = {
        "title": normalized(track.get("title", "")),
        "artist": normalized(track.get("artist", "")),
        "album": normalized(track.get("album", "")),
        "duration": round(float(track.get("duration", 0))),
    }
    payload = json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def lock_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.lock"


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    os.replace(temporary, path)


def read_cache(key: str) -> dict[str, Any] | None:
    path = cache_path(key)
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        log_error(f"Could not read cache {path}: {exc}")
        try:
            path.unlink()
        except OSError:
            pass
        return None


def api_request(endpoint: str, params: dict[str, Any]) -> Any:
    clean_params = {key: value for key, value in params.items() if value not in {None, ""}}
    url = f"{API_BASE}/{endpoint}?{urllib.parse.urlencode(clean_params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=NETWORK_TIMEOUT_SECONDS) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise RuntimeError(f"LRCLIB returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach LRCLIB: {exc.reason}") from exc


def candidate_score(track: dict[str, Any], candidate: dict[str, Any]) -> float:
    title_score = similarity(track.get("title", ""), candidate.get("trackName", ""))
    artist_score = similarity(track.get("artist", ""), candidate.get("artistName", ""))
    album_score = similarity(track.get("album", ""), candidate.get("albumName", ""))

    duration = float(track.get("duration", 0) or 0)
    candidate_duration = float(candidate.get("duration", 0) or 0)
    if duration and candidate_duration:
        duration_score = max(0.0, 1.0 - abs(duration - candidate_duration) / 20.0)
    else:
        duration_score = 0.5

    if title_score < 0.45 or artist_score < 0.35:
        return -1.0
    return 0.55 * title_score + 0.30 * artist_score + 0.10 * album_score + 0.05 * duration_score


def choose_candidate(track: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    usable = [
        item
        for item in candidates
        if isinstance(item, dict) and (item.get("syncedLyrics") or item.get("instrumental"))
    ]
    if not usable:
        return None
    ranked = sorted(
        ((candidate_score(track, item), item) for item in usable),
        key=lambda pair: pair[0],
        reverse=True,
    )
    best_score, best = ranked[0]
    return best if best_score >= 0.58 else None


def fetch_lyrics_record(track: dict[str, Any]) -> dict[str, Any] | None:
    exact_params = {
        "track_name": track.get("title", ""),
        "artist_name": track.get("artist", ""),
        "album_name": track.get("album", ""),
        "duration": round(float(track.get("duration", 0))) or None,
    }
    exact = api_request("get", exact_params)
    if isinstance(exact, dict) and (exact.get("syncedLyrics") or exact.get("instrumental")):
        return exact

    search_params = {
        "track_name": track.get("title", ""),
        "artist_name": track.get("artist", ""),
        "album_name": track.get("album", ""),
    }
    results = api_request("search", search_params)
    if isinstance(results, list):
        selected = choose_candidate(track, results)
        if selected:
            return selected

    broad_results = api_request(
        "search",
        {"q": f"{track.get('title', '')} {track.get('artist', '')}".strip()},
    )
    if isinstance(broad_results, list):
        return choose_candidate(track, broad_results)
    return None


def background_fetch(key: str, track: dict[str, Any]) -> None:
    lock = lock_path(key)
    try:
        try:
            record = fetch_lyrics_record(track)
            now = time.time()
            if record is None:
                payload = {
                    "status": "not_found",
                    "fetched_at": now,
                    "retry_after": now + RETRY_NOT_FOUND_SECONDS,
                }
            elif record.get("instrumental"):
                payload = {
                    "status": "instrumental",
                    "fetched_at": now,
                    "retry_after": now + RETRY_NOT_FOUND_SECONDS,
                    "record": record,
                }
            else:
                payload = {
                    "status": "ok",
                    "fetched_at": now,
                    "record": record,
                }
            atomic_write_json(cache_path(key), payload)
        except Exception as exc:
            now = time.time()
            log_error(f"Lyrics fetch failed for {track.get('artist')} — {track.get('title')}: {exc}")
            atomic_write_json(
                cache_path(key),
                {
                    "status": "network_error",
                    "message": str(exc),
                    "fetched_at": now,
                    "retry_after": now + RETRY_NETWORK_ERROR_SECONDS,
                },
            )
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def start_background_fetch(key: str, track: dict[str, Any]) -> bool:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    lock = lock_path(key)

    try:
        if lock.exists() and time.time() - lock.stat().st_mtime > LOCK_MAX_AGE_SECONDS:
            lock.unlink()
    except OSError:
        pass

    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
    except FileExistsError:
        return False

    payload = base64.urlsafe_b64encode(
        json.dumps(track, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    try:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--fetch", key, payload],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
        return True
    except Exception:
        try:
            lock.unlink()
        except OSError:
            pass
        raise


def parse_lrc(synced_lyrics: str) -> list[tuple[float, str]]:
    offset_match = OFFSET_RE.search(synced_lyrics)
    offset_seconds = float(offset_match.group(1)) / 1000.0 if offset_match else 0.0
    parsed: list[tuple[float, str]] = []

    for raw_line in synced_lyrics.splitlines():
        timestamps = TIMESTAMP_RE.findall(raw_line)
        if not timestamps:
            continue
        text = TIMESTAMP_RE.sub("", raw_line)
        text = ENHANCED_TIMESTAMP_RE.sub("", text).strip()
        if not text:
            continue
        for minutes, seconds in timestamps:
            timestamp = int(minutes) * 60 + float(seconds) + offset_seconds
            parsed.append((max(timestamp, 0.0), text))

    parsed.sort(key=lambda item: item[0])
    deduplicated: list[tuple[float, str]] = []
    for timestamp, text in parsed:
        if deduplicated and abs(deduplicated[-1][0] - timestamp) < 0.001:
            deduplicated[-1] = (timestamp, text)
        else:
            deduplicated.append((timestamp, text))
    return deduplicated


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


def marquee(text: str, elapsed: float, width: int) -> str:
    if width <= 0 or display_width(text) <= width:
        return text
    if not SCROLL_LONG_LINES:
        return crop_cells(text, 0, max(width - 1, 1)) + "…"

    maximum_offset = max(display_width(text) - width, 0)
    moving_time = max(0.0, elapsed - SCROLL_DELAY_SECONDS)
    offset = min(int(moving_time * SCROLL_CELLS_PER_SECOND), maximum_offset)
    return crop_cells(text, offset, width)


def current_lyric_line(
    lines: list[tuple[float, str]], position: float
) -> tuple[str | None, float]:
    if not lines:
        return None, 0.0
    timestamps = [item[0] for item in lines]
    index = bisect.bisect_right(timestamps, position) - 1
    if index < 0:
        return None, 0.0
    timestamp, text = lines[index]
    return text, max(position - timestamp, 0.0)


def render_widget(track: dict[str, Any], cached: dict[str, Any] | None) -> str:
    state = track.get("state", "")
    title = track.get("title", "") or "Apple Music"

    if cached is None:
        return f"⌛ {title}"

    status = cached.get("status")
    if status == "instrumental":
        return "♪ Instrumental"
    if status == "not_found":
        return "♪ No synced lyrics"
    if status == "network_error":
        return "⚠ Lyrics network error"
    if status != "ok":
        return "♪ Lyrics unavailable"

    record = cached.get("record") or {}
    synced_lyrics = record.get("syncedLyrics") or ""
    lines = parse_lrc(synced_lyrics)
    position = float(track.get("position", 0.0)) + SYNC_OFFSET_SECONDS
    lyric, elapsed = current_lyric_line(lines, position)

    prefix = "Ⅱ " if state == "paused" else "♪ "
    if lyric is None:
        return prefix + title
    available_width = max(VIEWPORT_WIDTH - display_width(prefix), 8)
    return prefix + marquee(lyric, elapsed, available_width)


def widget_main() -> int:
    try:
        track = read_apple_music()
    except PermissionError:
        emit("⚠ Allow BTT → Music")
        return 0
    except Exception as exc:
        log_error(str(exc))
        emit("⚠ Apple Music error")
        return 0

    state = track.get("state")
    if state == "not_running":
        emit("♪ Music closed")
        return 0
    if state == "stopped":
        emit("♪ Not playing")
        return 0
    if not track.get("title"):
        emit("♪ No track")
        return 0

    key = track_cache_key(track)
    cached = read_cache(key)
    now = time.time()

    if cached is None:
        try:
            start_background_fetch(key, track)
        except Exception as exc:
            log_error(f"Could not start background fetch: {exc}")
        emit(render_widget(track, None))
        return 0

    retry_after = float(cached.get("retry_after", 0) or 0)
    if retry_after and now >= retry_after:
        try:
            cache_path(key).unlink()
        except OSError:
            pass
        try:
            start_background_fetch(key, track)
        except Exception as exc:
            log_error(f"Could not retry background fetch: {exc}")
        emit(render_widget(track, None))
        return 0

    emit(render_widget(track, cached))
    return 0


def fetch_mode(arguments: list[str]) -> int:
    if len(arguments) != 2:
        return 2
    key, encoded_payload = arguments
    try:
        track = json.loads(base64.urlsafe_b64decode(encoded_payload.encode("ascii")).decode("utf-8"))
        background_fetch(key, track)
        return 0
    except Exception as exc:
        log_error(f"Background fetch process failed: {exc}")
        try:
            lock_path(key).unlink()
        except OSError:
            pass
        return 1


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--fetch":
        return fetch_mode(sys.argv[2:])
    return widget_main()


if __name__ == "__main__":
    raise SystemExit(main())

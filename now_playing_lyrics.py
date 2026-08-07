#!/usr/bin/env python3
"""BetterTouchTool Touch Bar lyrics widget for Apple Music.

Apple Music supplies track metadata and playback position through AppleScript.
A token-free Chinese lyrics aggregator (LrcAPI) and LRCLIB supply synchronized LRC lyrics, which are cached locally.

The widget path never blocks. BetterTouchTool runs widget scripts one at a
time and its refresh_widget command waits for them, so a slow run freezes
every other widget's tap-refresh too. Neither of the two slow things happens
inline: a cache miss starts a background lyrics fetch, and Apple Music is
sampled by a detached helper whose last sample the widget reads from disk.
"""

from __future__ import annotations

import base64
import bisect
import difflib
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, NamedTuple

# urllib and concurrent.futures cost about 50 ms to import — a quarter of a
# widget tick, and BetterTouchTool is blocked for every millisecond of it.
# Only the background fetch needs them, so it imports them itself.

# A widget run is timed from here rather than from widget_main, because what
# matters is how long BetterTouchTool was blocked, not how long the render
# took. Interpreter startup and the imports above cost a further ~65 ms that
# nothing inside the script can measure; treat traced widget times as that
# much short of the true figure. The constant does not matter for spotting a
# freeze, which shows up as a gap between runs or as one run taking seconds.
_LOADED_AT = time.monotonic()

try:
    from opencc import OpenCC  # type: ignore
except ImportError:
    OpenCC = None  # type: ignore

_OPENCC_CONVERTERS: list[Any] = []
if OpenCC is not None:
    try:
        _OPENCC_CONVERTERS = [OpenCC("t2s"), OpenCC("s2t")]
    except Exception:
        _OPENCC_CONVERTERS = []

# ---- User-tunable defaults -------------------------------------------------
# The Touch Bar renders a proportional font, so a pixel budget tracks the
# real row width better than a raw character count. PIXELS_PER_CELL
# calibrates character "cells" (narrow = 1, CJK/wide = 2, see
# character_width) to pixels; tune it if lines wrap earlier or later than
# they visibly need to. Setting BTT_LYRICS_WIDTH still overrides both and
# picks a width directly in cells, as before.
MAX_LYRIC_WIDTH_PX = float(os.environ.get("BTT_LYRICS_MAX_WIDTH_PX", "200"))
PIXELS_PER_CELL = float(os.environ.get("BTT_LYRICS_PX_PER_CELL", "7.0"))
_viewport_width_override = os.environ.get("BTT_LYRICS_WIDTH")
VIEWPORT_WIDTH = (
    int(_viewport_width_override)
    if _viewport_width_override is not None
    else max(round(MAX_LYRIC_WIDTH_PX / PIXELS_PER_CELL), 1)
)
SYNC_OFFSET_SECONDS = float(os.environ.get("BTT_LYRICS_OFFSET", "0.0"))
SCROLL_LONG_LINES = os.environ.get("BTT_LYRICS_SCROLL", "1") not in {
    "0",
    "false",
    "False",
}
SCROLL_DELAY_SECONDS = float(os.environ.get("BTT_LYRICS_SCROLL_DELAY", "0.8"))
SCROLL_CELLS_PER_SECOND = float(os.environ.get("BTT_LYRICS_SCROLL_RATE", "5.0"))
# An over-wide lyric line is wrapped onto extra rows at these punctuation
# marks before falling back to scrolling. The mark stays on the row it ends.
# Spaces are a second choice, used only when punctuation alone cannot make the
# rows fit, because many LRC files separate phrases with spaces and no commas.
LINE_BREAK_PUNCTUATION = os.environ.get("BTT_LYRICS_BREAK_CHARS", "，,、")
BREAK_ON_SPACE = os.environ.get("BTT_LYRICS_BREAK_ON_SPACE", "1") not in {
    "0",
    "false",
    "False",
}
MAX_LYRIC_ROWS = int(os.environ.get("BTT_LYRICS_MAX_ROWS", "2"))
# Wider than the prefix's character count on purpose: the Touch Bar's
# proportional font renders a symbol like "♪ " wider than two plain spaces,
# so matching character-for-character still looks left-shifted in practice.
CONTINUATION_INDENT = os.environ.get("BTT_LYRICS_INDENT", "     ")
NETWORK_TIMEOUT_SECONDS = float(os.environ.get("BTT_LYRICS_NETWORK_TIMEOUT", "4.0"))
APPLE_MUSIC_TIMEOUT_SECONDS = float(
    os.environ.get("BTT_LYRICS_APPLE_MUSIC_TIMEOUT", "1.5")
)

CACHE_DIR = Path.home() / "Library" / "Caches" / "BTTNowPlayingLyrics"
WIDGET_LOCK_PATH = CACHE_DIR / "widget.lock"
SAMPLER_LOCK_PATH = CACHE_DIR / "sampler.lock"
STATE_PATH = CACHE_DIR / "state.json"
LAST_TEXT_PATH = CACHE_DIR / "last.txt"
TRACE_PATH = CACHE_DIR / "trace.tsv"
WATCH_PATH = CACHE_DIR / "watch.tsv"
# Where the shell widgets trace, via lib/btt-widget.sh. --report reads both,
# because "did every widget stop at once, or just this one?" is the question
# that separates a BetterTouchTool problem from a script problem.
SHELL_TRACE_PATH = Path(
    os.environ.get("BTT_WIDGET_CACHE_DIR", str(Path.home() / "Library/Caches/btt-widgets"))
) / "trace.tsv"
# One line per run at a one second interval is roughly 5 MB a day, so the cap
# holds several hours -- long enough to still cover a freeze noticed later.
TRACE_MAX_BYTES = int(os.environ.get("BTT_LYRICS_TRACE_MAX_BYTES", "4000000"))
TRACE_ENABLED = os.environ.get("BTT_LYRICS_TRACE", "1") not in {"0", "false", "False"}
LRCLIB_API_BASE = "https://lrclib.net/api"
LRCAPI_API_BASE = "https://api.lrc.cx/api/v1/lyrics"
ENABLE_LRCAPI = os.environ.get("BTT_LYRICS_LRCAPI", "1") not in {"0", "false", "False"}
LRCAPI_TIMEOUT_SECONDS = float(os.environ.get("BTT_LYRICS_LRCAPI_TIMEOUT", "2.5"))
LRCAPI_MAX_ADVANCE_QUERIES = int(os.environ.get("BTT_LYRICS_LRCAPI_MAX_ADVANCE", "4"))
LRCAPI_MAX_SINGLE_QUERIES = int(os.environ.get("BTT_LYRICS_LRCAPI_MAX_SINGLE", "2"))
LRCLIB_MAX_SEARCH_QUERIES = int(os.environ.get("BTT_LYRICS_LRCLIB_MAX_SEARCH", "7"))
FETCH_TIMEOUT_SECONDS = float(os.environ.get("BTT_LYRICS_FETCH_TIMEOUT", "20"))
# Providers and their individual queries run concurrently: LrcAPI's public
# endpoint regularly needs several seconds per query, and run one after the
# other those seconds are exactly how long the widget shows an hourglass.
FETCH_WORKERS = max(int(os.environ.get("BTT_LYRICS_FETCH_WORKERS", "6")), 1)
USER_AGENT = "BTT-NowPlaying-Lyrics/6.0 (personal macOS Touch Bar widget)"
LOCK_MAX_AGE_SECONDS = max(FETCH_TIMEOUT_SECONDS + 15.0, 30.0)
WIDGET_LOCK_MAX_AGE_SECONDS = max(APPLE_MUSIC_TIMEOUT_SECONDS * 4.0, 3.0)
SAMPLER_LOCK_MAX_AGE_SECONDS = max(APPLE_MUSIC_TIMEOUT_SECONDS * 2.0, 3.0)
# How old the newest Apple Music sample may get before the widget asks for a
# fresh one, and before it stops trusting the sample it has.
STATE_REFRESH_SECONDS = float(os.environ.get("BTT_LYRICS_STATE_REFRESH", "0.5"))
STATE_MAX_AGE_SECONDS = float(os.environ.get("BTT_LYRICS_STATE_MAX_AGE", "8.0"))
# An hourglass is only honest for as long as a fetch plausibly takes. After
# that the track title is the more useful thing to look at.
PENDING_HOURGLASS_SECONDS = float(os.environ.get("BTT_LYRICS_PENDING_WAIT", "1.5"))
# How long a swipe's next/previous-track command is followed while Apple Music
# settles on the new track, and how often it is checked meanwhile.
TRACK_FOLLOW_SECONDS = float(os.environ.get("BTT_LYRICS_TRACK_FOLLOW", "3.0"))
TRACK_FOLLOW_INTERVAL = float(os.environ.get("BTT_LYRICS_TRACK_FOLLOW_STEP", "0.15"))
# The Lyrics widget's BTT UUID, so a track change can repaint it at once.
LYRICS_WIDGET_UUID = os.environ.get("BTT_LYRICS_WIDGET_UUID", "")
RETRY_NETWORK_ERROR_SECONDS = 90
LRCLIB_RETRY_ATTEMPTS = int(os.environ.get("BTT_LYRICS_LRCLIB_RETRIES", "2"))
LRCLIB_RETRY_BACKOFF_SECONDS = 0.5
# Upstream hiccups worth a second attempt rather than a cached failure.
TRANSIENT_HTTP_STATUS = {408, 425, 429, 500, 502, 503, 504}
RETRY_NOT_FOUND_SECONDS = 6 * 60 * 60
MATCHER_VERSION = 6

# Metadata aliases commonly used by streaming catalogs and community lyric
# databases. Add your own groups in lyrics_aliases.json next to this script.
BUILTIN_ALIAS_GROUPS = [
    ["張懸", "张悬", "Deserts Chang", "安溥", "Anpu"],
    [
        "银河快递",
        "銀河快遞",
        "Galaxy Express",
        "银河快递(Galaxy Express)",
        "銀河快遞(Galaxy Express)",
    ],
]
ALIASES_PATH = Path(
    os.environ.get(
        "BTT_LYRICS_ALIASES",
        str(Path(__file__).resolve().with_name("lyrics_aliases.json")),
    )
)

LOCAL_LYRICS_DIR = Path(
    os.environ.get(
        "BTT_LYRICS_LOCAL_DIR",
        str(Path(__file__).resolve().with_name("lyrics")),
    )
)

# While a stream starts, Apple Music reports a placeholder track with no
# artist. Looking those up wastes a fetch and caches a miss under a key the
# real track will never use again.
PLACEHOLDER_TITLES = {
    "loading",
    "connecting",
    "buffering",
    "",
}

UNIT_SEPARATOR = "\x1f"
TIMESTAMP_RE = re.compile(r"\[(\d{1,3}):(\d{2}(?:\.\d{1,3})?)\]")
ENHANCED_TIMESTAMP_RE = re.compile(r"<\d{1,3}:\d{2}(?:\.\d{1,3})?>")
OFFSET_RE = re.compile(r"\[offset:([+-]?\d+)\]", re.IGNORECASE)

APPLE_MUSIC_SCRIPT = r"""
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
"""


def emit(text: str) -> None:
    """Print the widget text for BetterTouchTool, one row per line.

    Each row is whitespace-compacted independently so a wrapped lyric keeps its
    leading indent; every other message is still a single row.
    """
    rows: list[str] = []
    for raw_row in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        indent = raw_row[: len(raw_row) - len(raw_row.lstrip(" "))]
        body = " ".join(raw_row.split())
        if body:
            rows.append(indent + body)
    output = "\n".join(rows) or "♪"
    print(output)
    remember_output(output)


def remember_output(text: str) -> None:
    """Keep the last printed value, so a skipped run can reprint it.

    A run that cannot take the widget lock has nothing of its own to show;
    printing this beats blanking the widget for a tick.
    """
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        LAST_TEXT_PATH.write_text(text, encoding="utf-8")
    except OSError:
        pass


def emit_last_output() -> None:
    """Reprint the previous value, for a run with nothing of its own to show."""
    try:
        previous = LAST_TEXT_PATH.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        previous = ""
    print(previous or "♪")

    # Touched even though the text is unchanged, so that this file's age
    # always means "how long since BetterTouchTool last ran the widget".
    # Without this a widget that reprints every tick is indistinguishable
    # from one BTT has stopped running, which is the single most useful
    # thing to know when the Touch Bar appears frozen.
    try:
        os.utime(LAST_TEXT_PATH, None)
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
    if not TRACE_ENABLED:
        return

    elapsed_ms = (time.monotonic() - started) * 1000.0
    extra = " ".join(f"{key}={value}" for key, value in fields.items())
    # Same columns as lib/btt-widget.sh writes, so one --report covers every
    # widget: a freeze is a property of BetterTouchTool, not of one script,
    # and it is only diagnosable with all of them side by side.
    line = (
        f"{time.time():.3f}\tlyrics\t{mode}\t{elapsed_ms:.0f}\t{outcome}\t{extra}\n"
    )

    oversized = False
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with TRACE_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line)
            # tell() after the write gives the size without a second syscall.
            oversized = handle.tell() > TRACE_MAX_BYTES
    except OSError:
        return

    if oversized:
        # One generation kept, so the trace costs at most twice the cap and a
        # freeze is still inspectable just after a rotation.
        try:
            os.replace(TRACE_PATH, TRACE_PATH.with_name(TRACE_PATH.name + ".1"))
        except OSError:
            pass


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
            ["/usr/bin/osascript", "-e", APPLE_MUSIC_SCRIPT],
            capture_output=True,
            text=True,
            timeout=APPLE_MUSIC_TIMEOUT_SECONDS,
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


def write_state(track: dict[str, Any]) -> None:
    atomic_write_json(STATE_PATH, {"sampled_at": time.time(), "track": track})


def read_state() -> tuple[dict[str, Any] | None, float]:
    """The newest Apple Music sample and how many seconds old it is."""
    try:
        with STATE_PATH.open("r", encoding="utf-8") as handle:
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


def current_track() -> dict[str, Any] | None:
    """The newest Apple Music sample, or None when there is not a usable one.

    The widget never queries Apple Music itself, not even as a fallback. An
    osascript round trip costs a few hundred milliseconds and stalls for
    seconds while a track changes -- and a fallback fires precisely when Music
    is being slow, so every tick would pay that cost, at a one second
    interval, on the single XPC service BetterTouchTool runs all widget
    scripts through. That starves this widget and swallows every other
    widget's tap-refresh with it.

    So sampling only ever happens in the detached helper, and a tick that
    finds nothing fresh reprints the last frame and waits for the next
    sample. Recovery costs nothing: a sampler is asked for on every tick.
    """
    global _SAMPLE_AGE

    track, age = read_state()
    _SAMPLE_AGE = age

    if age >= STATE_REFRESH_SECONDS:
        try:
            start_sampler()
        except Exception as exc:
            log_error(f"Could not start Apple Music sampler: {exc}")

    if track is not None and age <= STATE_MAX_AGE_SECONDS:
        return project_playback(track, age)
    return None


# How stale the sample this tick rendered from was. Kept for the trace, so
# that recording it costs nothing rather than a second read of the state.
_SAMPLE_AGE: float = float("inf")


def _last_sample_age_ms() -> str:
    if _SAMPLE_AGE == float("inf"):
        return "none"
    return f"{_SAMPLE_AGE * 1000:.0f}"


def is_placeholder_track(track: dict[str, Any]) -> bool:
    title = track.get("title", "").strip()
    if track.get("artist", "").strip():
        return False
    return title.casefold().rstrip(".…") in PLACEHOLDER_TITLES


VERSION_MARKERS = (
    "remaster",
    "remastered",
    "version",
    "edit",
    "mix",
    "live",
    "acoustic",
    "demo",
    "mono",
    "stereo",
    "deluxe",
    "bonus",
    "radio",
    "single",
    "現場",
    "现场",
    "演唱會",
    "演唱会",
    "錄音室",
    "录音室",
    "重製",
    "重制",
    "重新錄製",
    "重新录制",
    "專輯版",
    "专辑版",
    "單曲版",
    "单曲版",
)


def strip_version_annotations(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip()

    def keep_or_remove(match: re.Match[str]) -> str:
        body = match.group(1).casefold()
        return (
            "" if any(marker in body for marker in VERSION_MARKERS) else match.group(0)
        )

    value = re.sub(r"\(([^)]*)\)", keep_or_remove, value)
    value = re.sub(r"\[([^]]*)\]", keep_or_remove, value)
    value = re.sub(r"\b(?:feat|featuring|ft)\.?\s+.*$", "", value, flags=re.IGNORECASE)
    return " ".join(value.split())


def normalized(value: str) -> str:
    value = strip_version_annotations(value).casefold()
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def load_alias_groups() -> list[list[str]]:
    groups = [list(group) for group in BUILTIN_ALIAS_GROUPS]
    try:
        with ALIASES_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            for key, values in payload.items():
                if isinstance(values, str):
                    values = [values]
                if isinstance(values, list):
                    groups.append([str(key), *(str(item) for item in values)])
        elif isinstance(payload, list):
            for values in payload:
                if isinstance(values, list):
                    groups.append([str(item) for item in values])
    except FileNotFoundError:
        pass
    except Exception as exc:
        log_error(f"Could not read aliases from {ALIASES_PATH}: {exc}")
    return groups


def basic_metadata_variants(value: str) -> list[str]:
    base = value.strip()
    stripped = strip_version_annotations(base)
    variants = {item for item in (base, stripped) if item}
    for converter in _OPENCC_CONVERTERS:
        for item in list(variants):
            try:
                converted = converter.convert(item).strip()
                if converted:
                    variants.add(converted)
            except Exception:
                pass
    return sorted(variants, key=lambda item: (item != base, len(item), item.casefold()))


def metadata_variants(value: str) -> list[str]:
    base_variants = basic_metadata_variants(value)
    variants = set(base_variants)
    needles = {normalized(item) for item in base_variants}
    for group in load_alias_groups():
        normalized_group = {normalized(item) for item in group}
        if needles & normalized_group:
            variants.update(item for item in group if item)
    base = value.strip()
    return sorted(variants, key=lambda item: (item != base, len(item), item.casefold()))


def aliases_equivalent(left: str, right: str) -> bool:
    left_forms = {normalized(item) for item in basic_metadata_variants(left)}
    right_forms = {normalized(item) for item in basic_metadata_variants(right)}
    if left_forms & right_forms:
        return True
    for group in load_alias_groups():
        normalized_group = {normalized(item) for item in group}
        if left_forms & normalized_group and right_forms & normalized_group:
            return True
    return False


def similarity(left: str, right: str) -> float:
    return difflib.SequenceMatcher(None, normalized(left), normalized(right)).ratio()


def best_similarity(left: str, right: str) -> float:
    if not left.strip() or not right.strip():
        return 0.0
    if aliases_equivalent(left, right):
        return 1.0
    return max(
        similarity(left_variant, right_variant)
        for left_variant in basic_metadata_variants(left)
        for right_variant in basic_metadata_variants(right)
    )


def track_cache_key(track: dict[str, Any]) -> str:
    identity = {
        "title": normalized(track.get("title", "")),
        "artist": normalized(track.get("artist", "")),
        "album": normalized(track.get("album", "")),
        "duration": round(float(track.get("duration", 0))),
        "matcher_version": MATCHER_VERSION,
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


_FETCH_DEADLINE: float | None = None


def fetch_seconds_remaining() -> float:
    """Seconds left in the current background fetch, or infinity outside one."""
    if _FETCH_DEADLINE is None:
        return float("inf")
    return _FETCH_DEADLINE - time.monotonic()


def map_concurrently(
    function: Any, items: list[Any]
) -> list[tuple[Any, Exception | None]]:
    """Apply function to every item at once, keeping the input order.

    Results come back as (value, error) pairs, so one failing query cannot
    discard the results of the others, and ranking still sees the queries in
    the order they were composed.
    """
    from concurrent.futures import ThreadPoolExecutor

    if not items:
        return []
    if len(items) == 1:
        try:
            return [(function(items[0]), None)]
        except Exception as exc:
            return [(None, exc)]

    with ThreadPoolExecutor(max_workers=min(len(items), FETCH_WORKERS)) as pool:
        futures = [pool.submit(function, item) for item in items]
        collected: list[tuple[Any, Exception | None]] = []
        for future in futures:
            try:
                collected.append((future.result(), None))
            except Exception as exc:
                collected.append((None, exc))
        return collected


def lrclib_api_request(
    endpoint: str, params: dict[str, Any], attempts: int = LRCLIB_RETRY_ATTEMPTS
) -> Any:
    import urllib.error
    import urllib.parse
    import urllib.request

    clean_params = {
        key: value for key, value in params.items() if value not in {None, ""}
    }
    url = f"{LRCLIB_API_BASE}/{endpoint}?{urllib.parse.urlencode(clean_params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )

    # A single 5xx or timed-out read is usually a momentary upstream hiccup, so
    # retry briefly instead of caching a failure for the whole track.
    last_error: Exception | None = None
    for attempt in range(max(attempts, 1)):
        if attempt:
            backoff = LRCLIB_RETRY_BACKOFF_SECONDS * attempt
            if fetch_seconds_remaining() < backoff + NETWORK_TIMEOUT_SECONDS:
                break
            time.sleep(backoff)
        try:
            with urllib.request.urlopen(
                request, timeout=NETWORK_TIMEOUT_SECONDS
            ) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            last_error = RuntimeError(f"LRCLIB returned HTTP {exc.code}")
            if exc.code not in TRANSIENT_HTTP_STATUS:
                break
        except urllib.error.URLError as exc:
            last_error = RuntimeError(f"Could not reach LRCLIB: {exc.reason}")
        except (TimeoutError, OSError) as exc:
            last_error = RuntimeError(f"Could not reach LRCLIB: {exc}")

    raise last_error or RuntimeError("LRCLIB request failed")


def lrcapi_request(params: dict[str, Any]) -> Any:
    """Query the public token-free LrcAPI advance endpoint."""
    import urllib.error
    import urllib.parse
    import urllib.request

    clean_params = {
        key: value for key, value in params.items() if value not in {None, ""}
    }
    url = f"{LRCAPI_API_BASE}/advance?{urllib.parse.urlencode(clean_params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(
            request, timeout=LRCAPI_TIMEOUT_SECONDS
        ) as response:
            payload = response.read()
        if not payload:
            return []
        return json.loads(payload.decode("utf-8-sig"))
    except urllib.error.HTTPError as exc:
        if exc.code in {404, 422}:
            return []
        raise RuntimeError(f"LrcAPI returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach LrcAPI: {exc.reason}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("LrcAPI returned an invalid response") from exc


def lrcapi_single_request(params: dict[str, Any]) -> str:
    """Query LrcAPI's single-result endpoint, which returns raw LRC text."""
    import urllib.error
    import urllib.parse
    import urllib.request

    clean_params = {
        key: value for key, value in params.items() if value not in {None, ""}
    }
    url = f"{LRCAPI_API_BASE}/single?{urllib.parse.urlencode(clean_params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/plain, text/html"},
    )
    try:
        with urllib.request.urlopen(
            request, timeout=LRCAPI_TIMEOUT_SECONDS
        ) as response:
            payload = response.read()
        return payload.decode("utf-8-sig", errors="replace").strip()
    except urllib.error.HTTPError as exc:
        if exc.code in {404, 422}:
            return ""
        raise RuntimeError(f"LrcAPI single endpoint returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Could not reach LrcAPI single endpoint: {exc.reason}"
        ) from exc


def adapt_lrcapi_candidate(item: dict[str, Any]) -> dict[str, Any] | None:
    lyrics = str(item.get("lyrics") or "")
    if not TIMESTAMP_RE.search(lyrics):
        return None
    provider_id = str(
        item.get("id") or hashlib.sha256(lyrics.encode("utf-8")).hexdigest()[:20]
    )
    return {
        "id": f"lrcapi:{provider_id}",
        "providerId": provider_id,
        "source": "lrcapi",
        "trackName": str(item.get("title") or ""),
        "artistName": str(item.get("artist") or ""),
        "albumName": str(item.get("album") or ""),
        "duration": 0,
        "instrumental": False,
        "plainLyrics": None,
        "syncedLyrics": lyrics,
    }


def collect_lrcapi_candidates(track: dict[str, Any]) -> list[dict[str, Any]]:
    """Search Chinese providers through LrcAPI without an API token."""
    if not ENABLE_LRCAPI:
        return []

    titles = local_title_variants(track.get("title", ""))[:4]
    artists = metadata_variants(track.get("artist", ""))[:6]
    album_variants = local_title_variants(track.get("album", ""))[:3]
    queries: list[dict[str, str]] = []
    query_signatures: set[tuple[tuple[str, str], ...]] = set()

    def add_query(title: str, artist: str = "", album_name: str = "") -> None:
        query = {"title": title, "artist": artist, "album": album_name}
        signature = tuple(query.items())
        if title and signature not in query_signatures:
            query_signatures.add(signature)
            queries.append(query)

    if not titles:
        return []

    original_title = titles[0]
    stripped_title = titles[1] if len(titles) > 1 else original_title
    original_artist = artists[0] if artists else ""
    chinese_artists = [
        artist
        for artist in artists
        if any("\u3400" <= char <= "\u9fff" for char in artist)
    ]
    preferred_chinese_artist = (
        chinese_artists[0] if chinese_artists else original_artist
    )
    original_album = album_variants[0] if album_variants else ""
    stripped_album = album_variants[1] if len(album_variants) > 1 else original_album

    # High-value combinations first. In particular, pair a subtitle-free
    # Chinese title with the Chinese artist alias; v4 did not test this pair.
    add_query(original_title, original_artist, original_album)
    add_query(stripped_title, preferred_chinese_artist, stripped_album)
    add_query(stripped_title, preferred_chinese_artist)
    add_query(original_title, preferred_chinese_artist)
    add_query(stripped_title, original_artist)
    add_query(original_title, original_artist)

    # Add a small title × artist matrix, while keeping network work bounded.
    for title in titles[:3]:
        for artist in artists[:4]:
            add_query(title, artist)
    add_query(stripped_title)

    attempted = queries[: max(LRCAPI_MAX_ADVANCE_QUERIES, 0)]
    collected: list[dict[str, Any]] = []
    seen: set[str] = set()
    errors: list[str] = []

    for query, (payload, error) in zip(
        attempted, map_concurrently(lrcapi_request, attempted)
    ):
        if error is not None:
            errors.append(str(error))
            continue
        if not isinstance(payload, list):
            continue
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            candidate = adapt_lrcapi_candidate(raw)
            if candidate is None:
                continue
            candidate["sourceQuery"] = query
            identity = str(candidate.get("id"))
            if identity not in seen:
                seen.add(identity)
                collected.append(candidate)

    # The documented /single endpoint sometimes succeeds when /advance
    # returns an empty candidate array. Restrict it to strong title+artist
    # queries to reduce false matches.
    if not collected:
        single_queries = [query for query in queries if query.get("artist")][
            : max(LRCAPI_MAX_SINGLE_QUERIES, 0)
        ]
        for query, (lyrics, error) in zip(
            single_queries, map_concurrently(lrcapi_single_request, single_queries)
        ):
            if error is not None:
                errors.append(str(error))
                continue
            if not lyrics or not TIMESTAMP_RE.search(lyrics):
                continue
            provider_id = hashlib.sha256(
                (json.dumps(query, ensure_ascii=False, sort_keys=True) + lyrics).encode(
                    "utf-8"
                )
            ).hexdigest()[:20]
            collected.append(
                {
                    "id": f"lrcapi-single:{provider_id}",
                    "providerId": provider_id,
                    "source": "lrcapi-single",
                    "trackName": query["title"],
                    "artistName": query["artist"],
                    "albumName": query.get("album", ""),
                    "duration": track.get("duration", 0),
                    "instrumental": False,
                    "plainLyrics": None,
                    "syncedLyrics": lyrics,
                    "sourceQuery": query,
                }
            )
            break

    if not collected and attempted and len(errors) >= len(attempted):
        raise RuntimeError(errors[0])
    return collected


def choose_lrcapi_candidate(track: dict[str, Any]) -> dict[str, Any] | None:
    return choose_candidate(track, collect_lrcapi_candidates(track))


def candidate_score(track: dict[str, Any], candidate: dict[str, Any]) -> float:
    title_score = best_similarity(
        track.get("title", ""), candidate.get("trackName", "")
    )
    artist_score = best_similarity(
        track.get("artist", ""), candidate.get("artistName", "")
    )
    album_score = best_similarity(
        track.get("album", ""), candidate.get("albumName", "")
    )

    duration = float(track.get("duration", 0) or 0)
    candidate_duration = float(candidate.get("duration", 0) or 0)
    duration_error = (
        abs(duration - candidate_duration) if duration and candidate_duration else None
    )
    if duration_error is not None:
        duration_score = max(0.0, 1.0 - duration_error / 30.0)
    else:
        duration_score = 0.45

    # Title is the strongest identity signal. Permit catalog/community artist
    # aliases when the title is an excellent match and duration is close.
    if title_score < 0.45:
        return -1.0
    duration_close = duration_error is not None and duration_error <= 10.0
    artist_alias_safe = artist_score >= 0.75 and duration_close
    title_alias_safe = title_score >= 0.88 and duration_close
    if title_score < 0.55 and not artist_alias_safe:
        return -1.0
    if artist_score < 0.18 and not title_alias_safe:
        return -1.0

    return (
        0.62 * title_score
        + 0.18 * artist_score
        + 0.05 * album_score
        + 0.15 * duration_score
    )


def choose_candidate(
    track: dict[str, Any], candidates: list[dict[str, Any]]
) -> dict[str, Any] | None:
    usable = [
        item
        for item in candidates
        if isinstance(item, dict)
        and (item.get("syncedLyrics") or item.get("instrumental"))
    ]
    if not usable:
        return None
    ranked = sorted(
        ((candidate_score(track, item), item) for item in usable),
        key=lambda pair: pair[0],
        reverse=True,
    )
    best_score, best = ranked[0]
    return best if best_score >= 0.60 else None


def collect_search_candidates(track: dict[str, Any]) -> list[dict[str, Any]]:
    title_variants = metadata_variants(track.get("title", ""))[:4]
    artist_variants = metadata_variants(track.get("artist", ""))[:6]
    collected: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_results(results: Any) -> None:
        if not isinstance(results, list):
            return
        for item in results:
            if not isinstance(item, dict):
                continue
            identity = str(
                item.get("id")
                or (
                    normalized(item.get("trackName", "")),
                    normalized(item.get("artistName", "")),
                    round(float(item.get("duration", 0) or 0)),
                )
            )
            if identity not in seen:
                seen.add(identity)
                collected.append(item)

    queries: list[dict[str, str]] = []

    def add_query(query: dict[str, str]) -> None:
        if query.get("track_name") or query.get("q"):
            queries.append(query)

    if title_variants:
        original_title = title_variants[0]
        original_artist = artist_variants[0] if artist_variants else ""
        add_query({"track_name": original_title, "artist_name": original_artist})

    for title in title_variants[:3]:
        add_query({"track_name": title})

    if title_variants:
        title = title_variants[0]
        add_query({"q": title})
        for artist in artist_variants[1:5]:
            add_query({"q": f"{title} {artist}".strip()})

    attempted = queries[: max(LRCLIB_MAX_SEARCH_QUERIES, 0)]
    if fetch_seconds_remaining() < NETWORK_TIMEOUT_SECONDS:
        attempted = []

    errors: list[Exception] = []
    for results, error in map_concurrently(
        lambda query: lrclib_api_request("search", query), attempted
    ):
        # One failing query should not discard the others' results.
        if error is not None:
            errors.append(error)
        else:
            add_results(results)

    if not collected and errors:
        raise errors[0]
    return collected


def local_title_variants(value: str) -> list[str]:
    """Return filename-friendly title variants, including subtitle-free forms."""
    variants = set(metadata_variants(value))
    for item in list(variants):
        # Also accept trailing subtitles such as "(Chill)" / "（Chill）".
        stripped = re.sub(r"\s*[\(（][^\(\)（）]+[\)）]\s*$", "", item).strip()
        if stripped:
            variants.add(stripped)
    return sorted(
        variants, key=lambda item: (item != value.strip(), len(item), item.casefold())
    )


def local_lyrics_record(track: dict[str, Any]) -> dict[str, Any] | None:
    """Load a synchronized .lrc from the local lyrics directory when available."""
    if not LOCAL_LYRICS_DIR.is_dir():
        return None

    titles = local_title_variants(track.get("title", ""))
    artists = metadata_variants(track.get("artist", ""))
    candidate_paths: list[Path] = []
    seen: set[Path] = set()

    # Prefer deterministic names: "Artist - Title.lrc" or "Title.lrc".
    for title in titles:
        for filename in (f"{title}.lrc",):
            path = LOCAL_LYRICS_DIR / filename
            if path not in seen:
                seen.add(path)
                candidate_paths.append(path)
        for artist in artists:
            for separator in (" - ", " — "):
                path = LOCAL_LYRICS_DIR / f"{artist}{separator}{title}.lrc"
                if path not in seen:
                    seen.add(path)
                    candidate_paths.append(path)

    # Then scan the small local directory for equivalent simplified/traditional
    # metadata or slightly different punctuation.
    title_forms = {normalized(item) for item in titles if normalized(item)}
    artist_forms = {normalized(item) for item in artists if normalized(item)}
    try:
        for path in LOCAL_LYRICS_DIR.glob("*.lrc"):
            stem = normalized(path.stem)
            title_match = any(form and form in stem for form in title_forms)
            artist_match = not artist_forms or any(
                form and form in stem for form in artist_forms
            )
            if title_match and artist_match and path not in seen:
                seen.add(path)
                candidate_paths.append(path)
    except OSError as exc:
        log_error(f"Could not scan local lyrics directory {LOCAL_LYRICS_DIR}: {exc}")

    for path in candidate_paths:
        try:
            synced = path.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            continue
        except OSError as exc:
            log_error(f"Could not read local LRC {path}: {exc}")
            continue

        if not TIMESTAMP_RE.search(synced):
            log_error(f"Local lyrics file has no LRC timestamps: {path}")
            continue

        return {
            "id": f"local:{path.name}",
            "trackName": track.get("title", ""),
            "artistName": track.get("artist", ""),
            "albumName": track.get("album", ""),
            "duration": track.get("duration", 0),
            "instrumental": False,
            "plainLyrics": None,
            "syncedLyrics": synced,
            "source": "local",
            "localPath": str(path),
        }

    return None


def lrclib_exact_record(track: dict[str, Any]) -> dict[str, Any] | None:
    """Look the track up under its original catalog metadata.

    One attempt only: the search below is a better use of the next few
    seconds than retrying the narrowest possible query.
    """
    title_variants = metadata_variants(track.get("title", ""))[:3]
    artist_variants = metadata_variants(track.get("artist", ""))[:5]
    duration = round(float(track.get("duration", 0))) or None

    exact = lrclib_api_request(
        "get",
        {
            "track_name": title_variants[0]
            if title_variants
            else track.get("title", ""),
            "artist_name": artist_variants[0]
            if artist_variants
            else track.get("artist", ""),
            "album_name": track.get("album", ""),
            "duration": duration,
        },
        attempts=1,
    )
    if isinstance(exact, dict) and (
        exact.get("syncedLyrics") or exact.get("instrumental")
    ):
        return dict(exact)
    return None


def as_lrclib_record(record: dict[str, Any]) -> dict[str, Any]:
    record = dict(record)
    record.setdefault("source", "lrclib")
    record.setdefault("providerId", record.get("id"))
    return record


def lrclib_record(track: dict[str, Any]) -> dict[str, Any] | None:
    """LRCLIB's best synchronized match for the track.

    The exact lookup usually is the whole answer, so it runs on its own and
    the seven-query search stays unsent. A failure here must not skip that
    search: a transient upstream error on the narrowest query says nothing
    about whether the track has lyrics.
    """
    exact_error: Exception | None = None
    try:
        exact = lrclib_exact_record(track)
    except Exception as exc:
        log_error(f"LRCLIB exact lookup failed; trying search: {exc}")
        exact_error, exact = exc, None

    if exact is not None:
        return as_lrclib_record(exact)

    try:
        selected = choose_candidate(track, collect_search_candidates(track))
    except Exception as exc:
        # Both LRCLIB paths failed; surface the original error so the cache
        # records a short-lived network failure rather than "not found".
        raise exact_error or exc

    if selected is None:
        if exact_error is not None:
            raise exact_error
        return None
    return as_lrclib_record(selected)


def fetch_lyrics_record(track: dict[str, Any]) -> dict[str, Any] | None:
    # Provider order: user-maintained local LRC, token-free Chinese sources,
    # then the open LRCLIB database.
    from concurrent.futures import ThreadPoolExecutor

    local = local_lyrics_record(track)
    if local is not None:
        return local

    # LrcAPI's public endpoint routinely needs several seconds per query and
    # LRCLIB has its own bad minutes; waiting out one before starting the
    # other is most of what keeps a new track on the hourglass. They run
    # together, and LrcAPI still wins whenever it has an answer.
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        lrcapi_lookup = pool.submit(choose_lrcapi_candidate, track)
        lrclib_lookup = pool.submit(lrclib_record, track)

        try:
            lrcapi = lrcapi_lookup.result()
        except Exception as exc:
            log_error(f"LrcAPI lookup failed; using LRCLIB: {exc}")
            lrcapi = None
        if lrcapi is not None:
            return lrcapi

        return lrclib_lookup.result()
    finally:
        # Never wait on the provider whose answer is no longer wanted: the
        # cache write that ends the hourglass happens as soon as this returns.
        pool.shutdown(wait=False)


def background_fetch(key: str, track: dict[str, Any]) -> None:
    global _FETCH_DEADLINE

    lock = lock_path(key)
    started = time.monotonic()
    # Keep a small margin so retries and pending queries stop before SIGALRM.
    _FETCH_DEADLINE = time.monotonic() + max(FETCH_TIMEOUT_SECONDS - 3.0, 1.0)
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
                record = dict(record)
                record["parsedLines"] = parse_lrc(record.get("syncedLyrics") or "")
                payload = {
                    "status": "ok",
                    "fetched_at": now,
                    "record": record,
                }
            atomic_write_json(cache_path(key), payload)
            trace(
                "fetch",
                started,
                str(payload["status"]),
                source=str((record or {}).get("source", "-")),
            )
        except Exception as exc:
            now = time.time()
            log_error(
                f"Lyrics fetch failed for {track.get('artist')} — {track.get('title')}: {exc}"
            )
            atomic_write_json(
                cache_path(key),
                {
                    "status": "network_error",
                    "message": str(exc),
                    "fetched_at": now,
                    "retry_after": now + RETRY_NETWORK_ERROR_SECONDS,
                },
            )
            trace(
                "fetch", started, "network_error", reason=str(exc).split(":")[0][:40]
            )
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def acquire_lock(path: Path, max_age_seconds: float) -> bool:
    """Take an exclusive lock file, dropping one left behind by a dead run."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        if path.exists() and time.time() - path.stat().st_mtime > max_age_seconds:
            path.unlink()
    except OSError:
        pass

    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        return True
    except FileExistsError:
        return False


def clear_lock(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def spawn_helper(arguments: list[str]) -> None:
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )


def start_background_fetch(key: str, track: dict[str, Any]) -> bool:
    lock = lock_path(key)
    if not acquire_lock(lock, LOCK_MAX_AGE_SECONDS):
        return False

    payload = base64.urlsafe_b64encode(
        json.dumps(track, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    try:
        spawn_helper(["--fetch", key, payload])
        return True
    except Exception:
        clear_lock(lock)
        raise


def start_sampler() -> bool:
    """Ask a detached helper for a fresh Apple Music sample.

    The lock keeps one sample in flight at a time, so a run of quick widget
    ticks cannot pile up osascript processes behind an unresponsive Music.
    """
    if not acquire_lock(SAMPLER_LOCK_PATH, SAMPLER_LOCK_MAX_AGE_SECONDS):
        return False

    try:
        spawn_helper(["--sample"])
        return True
    except Exception:
        clear_lock(SAMPLER_LOCK_PATH)
        raise


def acquire_widget_lock() -> bool:
    return acquire_lock(WIDGET_LOCK_PATH, WIDGET_LOCK_MAX_AGE_SECONDS)


def release_widget_lock() -> None:
    try:
        if WIDGET_LOCK_PATH.read_text(encoding="utf-8") == str(os.getpid()):
            WIDGET_LOCK_PATH.unlink()
    except (FileNotFoundError, OSError):
        pass


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

    tiers = [LINE_BREAK_PUNCTUATION]
    if BREAK_ON_SPACE and " " not in LINE_BREAK_PUNCTUATION:
        tiers.append(LINE_BREAK_PUNCTUATION + " ")

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
    if not SCROLL_LONG_LINES:
        return crop_cells(text, 0, max(width - 1, 1)) + "…"

    maximum_offset = max(display_width(text) - width, 0)
    moving_time = max(0.0, elapsed - SCROLL_DELAY_SECONDS)
    offset = min(int(moving_time * SCROLL_CELLS_PER_SECOND), maximum_offset)
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


def fetch_waiting_seconds(key: str) -> float:
    """How long the in-flight fetch for this track has been running."""
    try:
        return max(time.time() - lock_path(key).stat().st_mtime, 0.0)
    except OSError:
        return float("inf")


def render_widget(
    track: dict[str, Any],
    cached: dict[str, Any] | None,
    waiting_seconds: float = 0.0,
) -> str:
    state = track.get("state", "")
    title = track.get("title", "") or "Apple Music"

    if cached is None:
        # An hourglass is only honest while a fetch could plausibly still
        # land. Past that it reads as a stuck widget, and the track title is
        # the more useful thing to leave on screen.
        marker = "⌛" if waiting_seconds <= PENDING_HOURGLASS_SECONDS else "♪"
        return f"{marker} {title}"

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
    lines = record.get("parsedLines") or parse_lrc(synced_lyrics)
    position = float(track.get("position", 0.0)) + SYNC_OFFSET_SECONDS
    lyric, elapsed, index = current_lyric_line(lines, position)

    prefix = "Ⅱ " if state == "paused" else "♪ "
    if lyric is None:
        return prefix + title

    first_width = max(VIEWPORT_WIDTH - display_width(prefix), 8)
    other_width = max(VIEWPORT_WIDTH - display_width(CONTINUATION_INDENT), 8)
    rows = wrap_lyric(lyric, [first_width, other_width], MAX_LYRIC_ROWS)

    # A short current line leaves a spare row rather than wrapping into it.
    # Fill that row with the next lyric (static, not scrolled) as a preview,
    # so the widget still shows two rows instead of a blank second line.
    if len(rows) == 1 and MAX_LYRIC_ROWS > 1 and index + 1 < len(lines):
        next_lyric = lines[index + 1][1]
        if display_width(next_lyric) > other_width:
            next_lyric = crop_cells(next_lyric, 0, max(other_width - 1, 1)) + "…"
        return (
            prefix
            + marquee(rows[0], elapsed, first_width)
            + "\n"
            + CONTINUATION_INDENT
            + next_lyric
        )

    return "\n".join(
        (prefix if row_index == 0 else CONTINUATION_INDENT)
        + marquee(row, elapsed, first_width if row_index == 0 else other_width)
        for row_index, row in enumerate(rows)
    )


def render_tick() -> str:
    """Render one widget frame. Returns the path taken, for the trace."""
    track = current_track()

    if track is None:
        # Nothing fresh to render: a sampler is already on its way, so
        # hold the last frame rather than blanking the widget for a tick.
        emit_last_output()
        return "no_sample"

    state = track.get("state")
    if state == "denied":
        emit("⚠ Allow BTT → Music")
        return "denied"
    if state in {"not_running", "stopped"} or not track.get("title"):
        emit("♪")
        return "idle"

    if is_placeholder_track(track):
        # Apple Music has not settled on the real track yet, and its
        # placeholder is not something any lyrics provider knows.
        emit(f"♪ {track.get('title', '')}")
        return "placeholder"

    key = track_cache_key(track)
    cached = read_cache(key)
    now = time.time()

    if cached is None:
        try:
            start_background_fetch(key, track)
        except Exception as exc:
            log_error(f"Could not start background fetch: {exc}")
        emit(render_widget(track, None, fetch_waiting_seconds(key)))
        return "pending"

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
        emit(render_widget(track, None, fetch_waiting_seconds(key)))
        return "retrying"

    emit(render_widget(track, cached))
    return cached.get("status") or "ok"


def widget_main() -> int:
    started = _LOADED_AT

    if not acquire_widget_lock():
        # Two runs overlapping means ticks are taking longer than the widget's
        # interval. That is the shape of a freeze building, so it is worth a
        # line of its own rather than being lost inside the normal path.
        emit_last_output()
        trace("widget", started, "locked")
        return 0

    outcome = "crashed"
    try:
        outcome = render_tick()
        return 0
    finally:
        release_widget_lock()
        trace("widget", started, outcome, sample_age_ms=_last_sample_age_ms())


def fetch_mode(arguments: list[str]) -> int:
    if len(arguments) != 2:
        return 2
    key, encoded_payload = arguments
    try:
        track = json.loads(
            base64.urlsafe_b64decode(encoded_payload.encode("ascii")).decode("utf-8")
        )

        def stop_fetch(_signum: int, _frame: Any) -> None:
            raise TimeoutError(f"Lyrics fetch exceeded {FETCH_TIMEOUT_SECONDS:g}s")

        signal.signal(signal.SIGALRM, stop_fetch)
        signal.setitimer(signal.ITIMER_REAL, max(FETCH_TIMEOUT_SECONDS, 1.0))
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
    uuid = arguments[0] if arguments else LYRICS_WIDGET_UUID
    previous_track, _ = read_state()
    previous_title = (previous_track or {}).get("title", "")

    deadline = time.monotonic() + TRACK_FOLLOW_SECONDS
    settled: dict[str, Any] | None = None

    while time.monotonic() < deadline:
        try:
            track = read_apple_music()
        except Exception as exc:
            log_error(f"Track-change sample failed: {exc}")
            time.sleep(TRACK_FOLLOW_INTERVAL)
            continue

        write_state(track)
        title = track.get("title", "")
        if title and title != previous_title and not is_placeholder_track(track):
            settled = track
            break

        # Repaint as we go: the position and the paused/playing marker are
        # already worth updating even before the new title lands.
        time.sleep(TRACK_FOLLOW_INTERVAL)

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


def sample_mode() -> int:
    """Refresh the Apple Music sample the widget renders from."""
    started = time.monotonic()
    try:
        track = read_apple_music()
        write_state(track)
        trace("sample", started, track.get("state", "?"))
        return 0
    except PermissionError:
        # The widget no longer talks to Apple Music at all, so this is the
        # only place the automation prompt can be discovered. Record it as a
        # state rather than logging it, or the widget has no way to say so.
        write_state({"state": "denied"})
        trace("sample", started, "denied")
        return 1
    except Exception as exc:
        # Leave the previous sample in place. Apple Music briefly refuses to
        # answer while a track changes, and rendering a slightly old sample
        # beats blanking the widget for the length of the hiccup.
        log_error(f"Apple Music sample failed: {exc}")
        # A slow or failing sampler is what makes the widget hold a stale
        # frame, so the reason belongs next to the widget's own timings.
        trace("sample", started, "failed", reason=str(exc).split(":")[0][:40])
        return 1
    finally:
        clear_lock(SAMPLER_LOCK_PATH)


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
    print(f"Watching BTT every {interval:g}s → {WATCH_PATH}\nCtrl-C to stop.")

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
                with WATCH_PATH.open("a", encoding="utf-8") as handle:
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
        content = WATCH_PATH.read_text(encoding="utf-8", errors="replace")
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
        TRACE_PATH.with_name(TRACE_PATH.name + ".1"),
        TRACE_PATH,
        SHELL_TRACE_PATH.with_name(SHELL_TRACE_PATH.name + ".1"),
        SHELL_TRACE_PATH,
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


def percentile_interval(ticks: list[Row], fraction: float) -> float:
    if len(ticks) < 2:
        return 1.0
    deltas = sorted(b.at - a.at for a, b in zip(ticks, ticks[1:]))
    index = min(int(len(deltas) * fraction), len(deltas) - 1)
    return max(deltas[index], 0.1)


def median_interval(ticks: list[Row]) -> float:
    return percentile_interval(ticks, 0.5)


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
        print(f"  lyrics: {TRACE_PATH}\n  others: {SHELL_TRACE_PATH}")
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


def diagnose_current() -> int:
    try:
        track = read_apple_music()
    except Exception as exc:
        print(f"Apple Music error: {exc}")
        return 1

    if track.get("state") in {"not_running", "stopped"}:
        print(f"Music state: {track.get('state')}")
        return 1

    print(json.dumps(track, ensure_ascii=False, indent=2))
    print(f"cache key: {track_cache_key(track)}")
    print(f"title variants: {metadata_variants(track.get('title', ''))}")
    print(f"artist variants: {metadata_variants(track.get('artist', ''))}")
    print(f"local lyrics directory: {LOCAL_LYRICS_DIR}")
    local = local_lyrics_record(track)
    if local is not None:
        print(f"LOCAL LRC: {local.get('localPath')}")
        return 0
    print("No matching local synchronized LRC.")
    print(f"LrcAPI enabled: {ENABLE_LRCAPI}")
    if ENABLE_LRCAPI:
        diagnostic_titles = local_title_variants(track.get("title", ""))[:4]
        diagnostic_artists = metadata_variants(track.get("artist", ""))[:6]
        print(f"LrcAPI title variants: {diagnostic_titles}")
        print(f"LrcAPI artist variants: {diagnostic_artists}")
        print(
            "LrcAPI limits: "
            f"{LRCAPI_MAX_ADVANCE_QUERIES} advance + "
            f"{LRCAPI_MAX_SINGLE_QUERIES} single, "
            f"{LRCAPI_TIMEOUT_SECONDS:g}s timeout each"
        )
        print("Searching token-free Chinese providers through LrcAPI …")
        try:
            lrcapi_candidates = collect_lrcapi_candidates(track)
            if lrcapi_candidates:
                ranked_lrcapi = sorted(
                    (
                        (candidate_score(track, item), item)
                        for item in lrcapi_candidates
                    ),
                    key=lambda pair: pair[0],
                    reverse=True,
                )
                for score, item in ranked_lrcapi[:10]:
                    print(
                        f"{score:.3f}  lrcapi  "
                        f"{item.get('artistName')} — {item.get('trackName')} "
                        f"[source={item.get('source')}, id={item.get('providerId')}, "
                        f"query={item.get('sourceQuery')}]"
                    )
                selected_lrcapi = choose_candidate(track, lrcapi_candidates)
                if selected_lrcapi is not None:
                    print(
                        "SELECTED LRCAPI: "
                        f"{selected_lrcapi.get('artistName')} — "
                        f"{selected_lrcapi.get('trackName')}"
                    )
                    return 0
            else:
                print("No synchronized LrcAPI candidates.")
        except Exception as exc:
            print(f"LrcAPI error: {exc}")

    print("Searching LRCLIB synchronously …")

    try:
        candidates = collect_search_candidates(track)
    except Exception as exc:
        print(f"LRCLIB search error: {exc}")
        return 1

    ranked = sorted(
        ((candidate_score(track, item), item) for item in candidates),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if not ranked:
        print("No LRCLIB candidates returned.")
        return 2

    for score, item in ranked[:10]:
        synced = "synced" if item.get("syncedLyrics") else "plain-only"
        print(
            f"{score:6.3f}  {synced:10}  "
            f"{item.get('artistName', '')} — {item.get('trackName', '')}  "
            f"[{float(item.get('duration', 0) or 0):.1f}s]"
        )

    selected = choose_candidate(track, candidates)
    if selected:
        print(
            "SELECTED: "
            f"{selected.get('artistName', '')} — {selected.get('trackName', '')}"
        )
        return 0
    print("Candidates existed, but none passed synchronized-lyrics matching.")
    return 2


def clear_current_cache() -> int:
    try:
        track = read_apple_music()
        key = track_cache_key(track)
        for path in (cache_path(key), lock_path(key)):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        print(f"Cleared current track cache: {key}")
        return 0
    except Exception as exc:
        print(f"Could not clear current cache: {exc}")
        return 1


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--fetch":
        return fetch_mode(sys.argv[2:])
    if len(sys.argv) >= 2 and sys.argv[1] == "--sample":
        return sample_mode()
    if len(sys.argv) >= 2 and sys.argv[1] == "--report":
        return report_mode(sys.argv[2:])
    if len(sys.argv) >= 2 and sys.argv[1] == "--watch":
        return watch_mode(sys.argv[2:])
    if len(sys.argv) >= 2 and sys.argv[1] == "--track-changed":
        return track_changed_mode(sys.argv[2:])
    if len(sys.argv) >= 2 and sys.argv[1] == "--diagnose":
        return diagnose_current()
    if len(sys.argv) >= 2 and sys.argv[1] == "--clear-current":
        return clear_current_cache()
    return widget_main()


if __name__ == "__main__":
    raise SystemExit(main())

"""Constants and environment-tunable settings shared across the widget.

Importing this module is the first thing every other module in the package
does, so LOADED_AT is captured here -- see __main__.py for why the timing
starts this early.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

# urllib and concurrent.futures cost about 50 ms to import — a quarter of a
# widget tick, and BetterTouchTool is blocked for every millisecond of it.
# Only the background fetch needs them, so it imports them itself.

# A widget run is timed from here rather than from widget_main, because what
# matters is how long BetterTouchTool was blocked, not how long the render
# took. Interpreter startup and the imports above cost a further ~65 ms that
# nothing inside the script can measure; treat traced widget times as that
# much short of the true figure. The constant does not matter for spotting a
# freeze, which shows up as a gap between runs or as one run taking seconds.
LOADED_AT = time.monotonic()

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
CONTINUATION_INDENT = os.environ.get("BTT_LYRICS_INDENT", "         ")
NETWORK_TIMEOUT_SECONDS = float(os.environ.get("BTT_LYRICS_NETWORK_TIMEOUT", "4.0"))
APPLE_MUSIC_TIMEOUT_SECONDS = float(
    os.environ.get("BTT_LYRICS_APPLE_MUSIC_TIMEOUT", "1.5")
)
# Falls back to the system-wide Now Playing info (via nowplaying-cli) when
# Apple Music has nothing playing, which is how QQ Music and other
# non-scriptable players are picked up. Off disables the fallback entirely.
ENABLE_MEDIA_REMOTE = os.environ.get("BTT_LYRICS_MEDIA_REMOTE", "1") not in {
    "0",
    "false",
    "False",
}
MEDIA_REMOTE_TIMEOUT_SECONDS = float(
    os.environ.get("BTT_LYRICS_MEDIA_REMOTE_TIMEOUT", "1.5")
)
# Already covered by the direct AppleScript path, and better: MediaRemote
# never reports Apple Music's live position, only a stale 0.
MEDIA_REMOTE_IGNORED_BUNDLE_IDS = {"com.apple.Music"}

REPO_DIR = Path(os.environ.get("BTT_REPO_DIR", Path(__file__).resolve().parents[2]))
CACHE_DIR = Path(
    os.environ.get("BTT_LYRICS_CACHE_DIR", str(REPO_DIR / "cache" / "lyrics"))
)
LOG_DIR = Path(os.environ.get("BTT_LOG_DIR", str(REPO_DIR / "logs")))
WIDGET_LOCK_PATH = CACHE_DIR / "widget.lock"
SAMPLER_LOCK_PATH = CACHE_DIR / "sampler.lock"
STATE_PATH = CACHE_DIR / "state.json"
# Where the hand-tracked MediaRemote position is kept between samples.
MEDIA_REMOTE_POSITION_PATH = CACHE_DIR / "media_remote_position.json"
LAST_TEXT_PATH = CACHE_DIR / "last.txt"
TRACE_PATH = LOG_DIR / "lyrics" / "trace.tsv"
WATCH_PATH = LOG_DIR / "lyrics" / "watch.tsv"
# Where the shell widgets trace, via lib/btt-widget.sh. --report reads both,
# because "did every widget stop at once, or just this one?" is the question
# that separates a BetterTouchTool problem from a script problem.
SHELL_TRACE_PATH = (
    Path(os.environ.get("BTT_WIDGET_LOG_DIR", str(LOG_DIR))) / "trace.tsv"
)
# One line per run at a one second interval is roughly 5 MB a day, so the cap
# holds several hours -- long enough to still cover a freeze noticed later.
TRACE_MAX_BYTES = int(os.environ.get("BTT_LYRICS_TRACE_MAX_BYTES", "4000000"))
TRACE_ENABLED = os.environ.get("BTT_LYRICS_TRACE", "1") not in {"0", "false", "False"}
LRCLIB_API_BASE = "https://lrclib.net/api"
LRCAPI_API_BASE = "https://api.lrc.cx/api/v1/lyrics"
ENABLE_LRCAPI = os.environ.get("BTT_LYRICS_LRCAPI", "1") not in {"0", "false", "False"}
LRCAPI_TIMEOUT_SECONDS = float(os.environ.get("BTT_LYRICS_LRCAPI_TIMEOUT", "2.5"))
CATALOG_LOOKUP_TIMEOUT_SECONDS = float(
    os.environ.get("BTT_LYRICS_CATALOG_LOOKUP_TIMEOUT", "4.0")
)
# Prefer LrcAPI briefly for Chinese-catalog coverage, then use an already-ready
# LRCLIB match instead of leaving the widget waiting for slower LrcAPI calls.
LRCAPI_PREFERENCE_SECONDS = max(
    float(os.environ.get("BTT_LYRICS_LRCAPI_PREFERENCE", "1.5")), 0.0
)
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
MATCHER_VERSION = 7

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
        str(Path(__file__).resolve().parent.with_name("lyrics_aliases.json")),
    )
)

LOCAL_LYRICS_DIR = Path(
    os.environ.get(
        "BTT_LYRICS_LOCAL_DIR",
        str(Path(__file__).resolve().parent.with_name("lyrics")),
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
        set trackSortName to (sort name of currentTrack) as text
    on error
        set trackSortName to ""
    end try

    try
        set trackGenre to (genre of currentTrack) as text
    on error
        set trackGenre to ""
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

    return currentState & sep & trackName & sep & trackSortName & sep & trackGenre & sep & artistName & sep & albumName & sep & trackDuration & sep & currentPosition
end tell
"""

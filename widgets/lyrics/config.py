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
# real row width better than a raw character count. Measured glyph advances
# are normalized to layout cells with PIXELS_PER_CELL; character_width falls
# back to narrow = 1 and CJK/wide = 2 when no measurement exists.
PIXELS_PER_CELL = float(os.environ.get("BTT_LYRICS_PX_PER_CELL", "7.0"))
# How much of the row the lyric gets is not fixed: it is whatever the Now
# Playing widget immediately to its left is not using, and that widget grows
# and shrinks with the title and album it is showing (see viewport.py).
# LYRIC_WIDTH_BUDGET_PX is what the two of them may take together.
#
# Calibrated against the current 13 px lyrics widget: the existing track leaves
# enough room for the next complete word without changing the widget font.
LYRIC_WIDTH_BUDGET_PX = float(os.environ.get("BTT_LYRICS_WIDTH_BUDGET_PX", "485"))
# Bounds on the lyric's own share. The floor stops a very long title from
# squeezing the lyric down to a few characters -- past it the row overflows
# the Touch Bar's right edge instead, which is at least still readable.
MIN_LYRIC_WIDTH_PX = float(os.environ.get("BTT_LYRICS_MIN_WIDTH_PX", "160"))
MAX_LYRIC_WIDTH_PX = float(os.environ.get("BTT_LYRICS_MAX_WIDTH_PX", "390"))
# Used until a track has been measured, and by any track whose measurement
# fails. Deliberately the conservative width this widget used before it
# measured anything, so a broken measurement degrades to the old behaviour
# rather than to an overflowing row.
FALLBACK_LYRIC_WIDTH_PX = float(os.environ.get("BTT_LYRICS_FALLBACK_WIDTH_PX", "200"))
# Setting BTT_LYRICS_WIDTH overrides the whole calculation and picks a width
# directly in cells, as before -- nothing is measured or spawned at all.
_viewport_width_override = os.environ.get("BTT_LYRICS_WIDTH")
VIEWPORT_WIDTH_OVERRIDE = (
    int(_viewport_width_override) if _viewport_width_override is not None else None
)

# The Now Playing widget's own settings, mirrored from its BTT trigger config
# in bttpreset/Default.bttpreset -- BTTTouchBarLine1Format,
# BTTTouchBarLine2Format, BTTTouchBarButtonFontSize and
# BTTTouchBarLine1MaxChars. Nothing keeps these in step automatically, so a
# change over in BTT belongs here too.
NOW_PLAYING_LINE_FORMATS = (
    os.environ.get("BTT_LYRICS_NOW_PLAYING_LINE1", "{title} - {album}"),
    os.environ.get("BTT_LYRICS_NOW_PLAYING_LINE2", "{artist} "),
)
# Mirrored from the Lyrics shell widget in bttpreset/Default.bttpreset.
LYRICS_FONT_SIZE = float(os.environ.get("BTT_LYRICS_FONT_SIZE", "13"))
NOW_PLAYING_FONT_SIZE = float(os.environ.get("BTT_LYRICS_NOW_PLAYING_FONT", "12"))
NOW_PLAYING_LINE_MAX_CHARS = int(
    os.environ.get("BTT_LYRICS_NOW_PLAYING_MAX_CHARS", "60")
)
# BTT stops widening that widget at BTTTBWidgetWidth (400), of which the
# album cover (BTTTouchBarItemIconWidth 30) and the gap after it
# (BTTTouchBarIconTextOffset 5) are not text. A longer title truncates rather
# than taking more of the row, so it stops costing the lyric anything either.
NOW_PLAYING_MAX_TEXT_PX = float(os.environ.get("BTT_LYRICS_NOW_PLAYING_MAX_PX", "365"))
# Generous: this runs in the detached helper, never on the widget path, and
# the only thing a tighter bound would buy is a missing measurement.
MEASURE_TIMEOUT_SECONDS = float(os.environ.get("BTT_LYRICS_MEASURE_TIMEOUT", "8.0"))
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
CONTINUATION_INDENT = os.environ.get("BTT_LYRICS_INDENT", "       ")
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
NETWORK_TIMEOUT_SECONDS = float(os.environ.get("BTT_LYRICS_NETWORK_TIMEOUT", "4.0"))
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
# The lyric's current share of the Touch Bar row, measured once per track by
# the --measure helper and read back by every tick. See viewport.py.
VIEWPORT_PATH = CACHE_DIR / "viewport.json"
VALUE_PATH = CACHE_DIR / "lyrics.value"
# What the last rendering tick put on screen, and when that frame is due to
# change. Written only by ticks that render something of their own, so an
# expired deadline in here is the freeze guard's evidence that the widget has
# stopped producing frames -- see render.write_render_receipt.
#
# It lives beside the trace rather than in CACHE_DIR because its only reader
# is actions/btt-freeze-guard.sh, which runs under launchd. That process gets
# EPERM opening anything under cache/ -- macOS gates ~/Documents, and only
# logs/ carries the com.apple.macl grant that lets it through. The widget
# itself runs under BetterTouchTool, which has the consent to write either.
RENDER_PATH = LOG_DIR / "lyrics" / "render.json"
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
ENABLE_LRCAPI = os.environ.get("BTT_LYRICS_LRCAPI", "1") not in {
    "0",
    "false",
    "False",
}
LRCAPI_TIMEOUT_SECONDS = float(os.environ.get("BTT_LYRICS_LRCAPI_TIMEOUT", "2.5"))
CATALOG_LOOKUP_TIMEOUT_SECONDS = float(
    os.environ.get("BTT_LYRICS_CATALOG_LOOKUP_TIMEOUT", "4.0")
)
LRCAPI_PREFERENCE_SECONDS = max(
    float(os.environ.get("BTT_LYRICS_LRCAPI_PREFERENCE", "1.5")), 0.0
)
LRCAPI_MAX_ADVANCE_QUERIES = int(os.environ.get("BTT_LYRICS_LRCAPI_MAX_ADVANCE", "4"))
LRCAPI_MAX_SINGLE_QUERIES = int(os.environ.get("BTT_LYRICS_LRCAPI_MAX_SINGLE", "2"))
LRCLIB_MAX_SEARCH_QUERIES = int(os.environ.get("BTT_LYRICS_LRCLIB_MAX_SEARCH", "7"))
NETEASE_API_BASE = "https://music.163.com"
ENABLE_NETEASE = os.environ.get("BTT_LYRICS_NETEASE", "1") not in {
    "0",
    "false",
    "False",
}
NETEASE_TIMEOUT_SECONDS = float(os.environ.get("BTT_LYRICS_NETEASE_TIMEOUT", "4.0"))
NETEASE_MAX_SEARCH_QUERIES = int(os.environ.get("BTT_LYRICS_NETEASE_MAX_SEARCH", "2"))
NETEASE_MAX_LYRIC_FETCHES = int(os.environ.get("BTT_LYRICS_NETEASE_MAX_LYRIC", "3"))
QQMUSIC_API_BASE = "https://c.y.qq.com"
ENABLE_QQMUSIC = os.environ.get("BTT_LYRICS_QQMUSIC", "1") not in {
    "0",
    "false",
    "False",
}
QQMUSIC_TIMEOUT_SECONDS = float(os.environ.get("BTT_LYRICS_QQMUSIC_TIMEOUT", "4.0"))
QQMUSIC_MAX_SEARCH_QUERIES = int(os.environ.get("BTT_LYRICS_QQMUSIC_MAX_SEARCH", "2"))
QQMUSIC_MAX_LYRIC_FETCHES = int(os.environ.get("BTT_LYRICS_QQMUSIC_MAX_LYRIC", "3"))
# Providers and their individual queries run concurrently so a slow public
# endpoint does not serialize every title and artist variant.
FETCH_WORKERS = max(int(os.environ.get("BTT_LYRICS_FETCH_WORKERS", "6")), 1)
USER_AGENT = "BTT-NowPlaying-Lyrics/6.0 (personal macOS Touch Bar widget)"
LRCLIB_RETRY_ATTEMPTS = int(os.environ.get("BTT_LYRICS_LRCLIB_RETRIES", "2"))
LRCLIB_RETRY_BACKOFF_SECONDS = 0.5
TRANSIENT_HTTP_STATUS = {408, 425, 429, 500, 502, 503, 504}
FETCH_TIMEOUT_SECONDS = float(os.environ.get("BTT_LYRICS_FETCH_TIMEOUT", "20"))
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
RETRY_CACHE_ERROR_SECONDS = 90
RETRY_NOT_FOUND_SECONDS = 6 * 60 * 60
# Bump this when provider behavior or cache-key inputs change. Apple-cache
# records carry the current track metadata even when duration matching chose a
# different cached TTML, so they cannot safely migrate across generations.
CACHE_KEY_VERSION = 3

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

# Apple Music caches the time-synced TTML it fetches for the catalog track it is
# about to display, as an ordinary NSURLCache entry. Reading it costs one local
# sqlite query, so it is worth trying before any network provider — but it is an
# LRU cache holding only the last handful of played tracks, never a library.
APPLE_MUSIC_CACHE_DB = Path(
    os.environ.get(
        "BTT_LYRICS_APPLE_CACHE_DB",
        str(Path.home() / "Library/Caches/com.apple.Music/Cache.db"),
    )
)
# Payloads above a few KB spill to a file named by the receiver_data column.
APPLE_MUSIC_CACHE_FS_DIR = APPLE_MUSIC_CACHE_DB.with_name("fsCachedData")
# AppleScript exposes no catalog ID, so the cached TTML is matched to the
# playing track by its declared duration. TTML commonly rounds the duration to
# whole seconds while AppleScript reports fractional seconds. Adjacent tracks
# on one album can sit ~2s apart, so keep the window below that gap.
APPLE_MUSIC_CACHE_DURATION_TOLERANCE = 2.0
# Beyond a handful the query stops being free, and older rows are stale anyway.
APPLE_MUSIC_CACHE_MAX_ROWS = 12

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

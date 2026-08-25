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
# stall, which shows up as a gap between runs or as one run taking seconds.
LOADED_AT = time.monotonic()

# ---- User-tunable defaults -------------------------------------------------
# The lyric renders at one fixed width, counted in layout cells: a narrow
# glyph is 1 cell and CJK/wide is 2 (see display/layout.py). The default
# matches the Lyrics widget's own BTTTBWidgetWidth (400) in
# bttpreset/Default.bttpreset at PIXELS_PER_CELL points per cell -- one cell
# is half a CJK glyph, so the calibration tracks the first row's 13 px font
# with a little headroom -- and the frame never overflows the widget's slot.
# BTT_LYRICS_WIDTH overrides the width directly in cells.
PIXELS_PER_CELL = float(os.environ.get("BTT_LYRICS_PX_PER_CELL", "7.0"))
LYRIC_WIDTH_CELLS = int(
    os.environ.get("BTT_LYRICS_WIDTH", str(max(round(400 / PIXELS_PER_CELL), 1)))
)
# BTT draws the widget's two rows into one fixed-height 22 px block: the font
# size configured in bttpreset/Default.bttpreset is the FIRST row's, and the
# second row renders at the remainder -- rows look equal at 11 px + 11 px.
# The Lyrics preset is 13 px, so the second row effectively renders at
# 22 - 13 = 9 px; width estimation must scale per row accordingly.
FIRST_ROW_FONT_PX = float(os.environ.get("BTT_LYRICS_ROW1_FONT_PX", "13.0"))
TWO_ROW_BLOCK_PX = float(os.environ.get("BTT_LYRICS_TWO_ROW_PX", "22.0"))
SECOND_ROW_FONT_PX = max(TWO_ROW_BLOCK_PX - FIRST_ROW_FONT_PX, 1.0)
# One layout cell is half a CJK glyph, i.e. half the row's font size in px.
SECOND_ROW_PX_PER_CELL = SECOND_ROW_FONT_PX / 2.0
# Seconds added to the reported playback position before picking the lyric
# line, i.e. how far ahead of the music the widget reads. The pipeline between
# the player's real position and the pixels on screen -- sampling the player,
# writing the state, BTT's own refresh -- costs the better part of a second, so
# a lyric picked from the raw position lands late. Half a second of look-ahead
# cancels that; raise it if lines still trail, lower it if they arrive early.
SYNC_OFFSET_SECONDS = float(os.environ.get("BTT_LYRICS_OFFSET", "0.5"))
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
# Row 2 carries no note symbol. Its line also renders at the second-row font
# -- the 22 px two-row block minus the first row's 13 px, i.e. 9 px -- so
# spaces on it are proportionally narrower and the calibrated indent is 7 of
# them.
CONTINUATION_INDENT = os.environ.get("BTT_LYRICS_INDENT", "       ")
# The gap after the first row's music-note symbol: a plain space. A text
# widget cannot express an exact pixel gap, so a single space is the
# calibrated separation before the lyric at the first row's 13 pt font.
NOTE_GAP = " "
# A track with no lyrics keeps its status symbol (♬ instrumental, ♩ not
# found) for the whole track, so it fades from MARKER_FADE_MAX white down
# to MARKER_FADE_MIN gray as the track plays, following the playback
# position rather than wall clock time.
MARKER_FADE_MAX = int(os.environ.get("BTT_LYRICS_MARKER_FADE_MAX", "255"))
MARKER_FADE_MIN = int(os.environ.get("BTT_LYRICS_MARKER_FADE_MIN", "80"))
# The shade every other frame renders in. BetterTouchTool keeps the last
# font_color a script widget set, so a frame that says nothing about colour
# inherits whatever the previous one asked for: after a marker faded to gray,
# the next track's lyrics came up in that same gray and stayed there. Every
# visible frame therefore carries a colour, and this is the one that means
# "nothing to fade" -- the full white lib/btt-widget.sh gives every other
# widget, read from the same environment variable.
WIDGET_FONT_COLOR = os.environ.get("BTT_WIDGET_COLOR", "255,255,255,255")
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
# The compiled nowplaying-state helper (actions/nowplaying-state.m) reports
# the true play/pause state of the system Now Playing app, which the raw
# MediaRemote dictionary cannot: QQ Music pushes playbackRate 1 even while
# paused. It lives in BTT's support directory, compiled there by hand; when
# it is missing the sampler falls back to nowplaying-cli + playbackRate,
# which scrolls through pauses.
NOWPLAYING_STATE_BIN = os.environ.get(
    "BTT_LYRICS_NOWPLAYING_STATE_BIN",
    str(Path.home() / "Library" / "Application Support" / "BTT" / "nowplaying-state"),
)
# How old the shared MediaRemote dictionary (MEDIA_REMOTE_RAW_PATH, defined
# with the other paths below) may be before the sampler asks for its own. The
# widget that writes it ticks every second.
MEDIA_REMOTE_RAW_MAX_AGE_SECONDS = float(
    os.environ.get("BTT_NOW_PLAYING_RAW_MAX_AGE", "5.0")
)
NETWORK_TIMEOUT_SECONDS = float(os.environ.get("BTT_LYRICS_NETWORK_TIMEOUT", "4.0"))
# Which MediaRemote session holders the fallback accepts. An allowlist, not
# a denylist, mirroring widgets/now-playing.sh: browsers are many and keep
# appearing, real players are few and fixed, so a YouTube tab can never
# hijack the lyric. Shares BTT_NOW_PLAYING_ALLOWED with now-playing.sh so
# one knob tunes both widgets; com.apple.Music is dropped here regardless --
# the direct AppleScript path already covers it, and better: MediaRemote
# never reports Music's live position, only a stale 0. Compared case-folded,
# like now-playing.sh, because vendors spell their own ids inconsistently
# (QQ Music registers com.tencent.QQMusicMac).
MEDIA_REMOTE_ALLOWED_BUNDLE_IDS = {
    bundle_id.lower()
    for bundle_id in os.environ.get(
        "BTT_NOW_PLAYING_ALLOWED", "com.apple.Music com.tencent.QQMusicMac"
    ).split()
    if bundle_id.lower() != "com.apple.music"
}

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
# The raw MediaRemote dictionary widgets/now-playing.sh writes on its own one
# second tick (via widgets/lib/media-remote.sh). The sampler reads it rather
# than running nowplaying-cli a second time: the two widgets sit side by side
# and want the same answer, and the call costs ~0.22 s.
#
# It is also the only source of the session holder's bundle id. The compiled
# state helper drops every NSData value, and the holder's id arrives inside
# one (ClientPropertiesData) -- so the helper alone cannot satisfy
# MEDIA_REMOTE_ALLOWED_BUNDLE_IDS above, and a sampler that trusted it for the
# id saw an empty string and rejected every track.
MEDIA_REMOTE_RAW_PATH = Path(
    os.environ.get(
        "BTT_NOW_PLAYING_RAW_PATH",
        str(
            Path(os.environ.get("BTT_WIDGET_CACHE_DIR", str(REPO_DIR / "cache")))
            / "now-playing.raw.json"
        ),
    )
)
# What the last rendering tick put on screen, and when that frame is due to
# change. Written only by ticks that render something of their own -- see
# render.write_render_receipt -- and read back by render.last_rendered_key,
# which is how the next tick's fresh process knows what is already up there.
#
# It lives beside the trace rather than in CACHE_DIR; nothing outside the
# widget reads it, and moving it would strand the receipt a fresh widget
# process expects to find.
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
# holds several hours -- long enough to still cover a stall noticed later.
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
# The Lyrics, Now Playing and Star widgets' BTT UUIDs, so the event watcher
# (the --sample helper and the --track-changed follow in cli.py) can run the
# closing sequence -- clear the lyric, then hide the row and the star with it.
# Mirrors actions/set-widget-variables.sh.
LYRICS_WIDGET_UUID = os.environ.get(
    "BTT_LYRICS_WIDGET_UUID", "E19BB023-5060-4A56-95C8-6E7402779870"
)
NOW_PLAYING_WIDGET_UUID = os.environ.get(
    "BTT_NOW_PLAYING_WIDGET_UUID", "710F54C5-25B0-4A2C-B960-D9C0FE78B1B7"
)
# The Star widget runs on a 10 s AppleScript tick of its own, so it is the one
# member of the row that would otherwise outlive a player by seconds -- see
# specs/design/STAR.md.
STAR_WIDGET_UUID = os.environ.get(
    "BTT_STAR_WIDGET_UUID", "A05C5D37-7EAA-4F7B-AC00-23183CC8C6A1"
)
# Where the event watcher records the track the closing sequence cleared
# (cache/lyrics-cleared, beside the other widgets' cache files). The Lyrics
# widget holds its frame empty while its state sample still matches this
# identity -- see render.cleared_while_sample_current.
CLEARED_MARKER_PATH = REPO_DIR / "cache" / "lyrics-cleared"
# The age cap on a closing marker. Normal markers are removed by the next
# watcher run once the state moves past the cleared identity; this bounds a
# marker left behind by a watcher that died mid-sequence, so a genuinely
# playing track re-appears even then.
CLEAR_HOLD_SECONDS = float(os.environ.get("BTT_LYRICS_CLEAR_HOLD", "15.0"))
RETRY_CACHE_ERROR_SECONDS = 90
RETRY_NOT_FOUND_SECONDS = 6 * 60 * 60
# Bump this when provider behavior or cache-key inputs change. Apple-cache
# records carry the current track metadata even when duration matching chose a
# different cached TTML, so they cannot safely migrate across generations.
# 4: the sampler supplies genre again, so the iTunes CN catalog lookup now
# reaches English-titled Mandopop tracks — records fetched while it could not
# (English-title searches picking near-duration lookalikes) are stale.
# 5: the catalog lookup also serves Cantopop tracks; records fetched while it
# only served Mandopop (e.g. "Unconditional" → "Unconditionally"/Katy Perry)
# are stale.
# 6: the catalog lookup now returns the CN artist name too (search_artist),
# so records fetched while romanized artists searched untranslated are stale.
# 7: candidate acceptance now requires a strict artist+title identity match
# with annotations trimmed on both sides (no more similarity floors that let
# a strong title vouch for a mismatched artist) -- records fetched while a
# lookalike song could be accepted are stale.
CACHE_KEY_VERSION = 7

# Community/catalog aliases for artist and title metadata, as a JSON file of
# alias groups shipped inside this package — there are no built-in groups, so
# the file is the single source of truth. Each group is a list of equivalent
# names.
ALIASES_PATH = Path(
    os.environ.get(
        "BTT_LYRICS_ALIASES",
        str(Path(__file__).resolve().parent / "lyrics_aliases.json"),
    )
)

# Hand-maintained .lrc files, which providers.local prefers over every remote
# lookup. A repo directory, NOT one inside the package: this holds the user's
# own files, and pointing it at the package would both mix data into the source
# tree and (as it did until 2026-08-12, via a copy of the ALIASES_PATH idiom
# above) resolve back to the package directory itself, where no .lrc can ever
# be found and the provider silently never matches.
LOCAL_LYRICS_DIR = Path(
    os.environ.get("BTT_LYRICS_LOCAL_DIR", str(REPO_DIR / "lyrics"))
)

# Apple Music caches the time-synced TTML it fetches for the catalog track it is
# about to display, as an ordinary NSURLCache entry. It is an LRU cache holding
# only the last handful of played tracks, never a library. A lookup is NOT one
# sqlite query: Music holds the DB open in WAL mode, so the reader copies the
# whole Cache.db (+ -wal/-shm sidecars) to a temp dir first to see uncommitted
# rows. The render path retries that copy on every tick while a track is
# not_found, which can stall the widget tick.
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

# The Apple TTML cache (providers/apple_cache.py) is a last resort: Music's
# URL cache holds the synced lyrics of whatever it played recently, and each
# lookup copies the whole cache DB to a temp dir, so it never preempts a
# remote provider (fetch.py) and the render path consults it at most once per
# track (render.py marks a miss). A track already captioned by QQ Music /
# NetEase / LrcAPI / LRCLIB never pays for the copy.
APPLE_CACHE_ENABLED = True

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
        set trackGenre to (genre of currentTrack) as text
    on error
        set trackGenre to ""
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

    return currentState & sep & trackName & sep & artistName & sep & trackGenre & sep & albumName & sep & trackDuration & sep & currentPosition
end tell
"""

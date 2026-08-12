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
# Calibrated against the preset layout in bttpreset/Default.bttpreset: the
# Now Playing widget's negative paddings -- BTTTouchBarItemPadding -5,
# BTTTouchBarFreeSpaceAfterButton -10, and the Lyrics widget's own
# BTTTouchBarItemPadding -5 -- let the pair's text use 20 px more than its
# nominal slot sum, so the budget is text width plus that margin credit.
# Measured at these settings (2026-08-11): a typical album/title row is
# ~268 px at the 11 pt Now Playing font, and a nine-word lyric line is
# ~284 px including the prefix at 13 pt; 570 leaves both on screen together
# with headroom, where 485 wrapped the same line with visible free space to
# its right.
LYRIC_WIDTH_BUDGET_PX = float(os.environ.get("BTT_LYRICS_WIDTH_BUDGET_PX", "570"))
# The Star widget (★/☆) draws only while Apple Music is the player; with
# QQ Music or anything else it renders empty and BTT hides it, freeing its
# row slot -- BTTTouchBarButtonWidth 100 minus the 5 px item padding -- for
# the Now Playing + lyric pair. They may take that much more of the row
# (~95 px, about three words at the 13 pt lyrics font), and the pair's
# budget grows by it only for non-Apple Music tracks
# (see viewport.lyric_budget_px).
LYRIC_EXTRA_WORD_PX = float(os.environ.get("BTT_LYRICS_EXTRA_WORD_PX", "95"))
# Bounds on the lyric's own share. The floor stops a very long title from
# squeezing the lyric down to a few characters -- past it the row overflows
# the Touch Bar's right edge instead, which is at least still readable.
# The cap is the Lyrics widget's own BTTTBWidgetWidth (400) in the preset.
MIN_LYRIC_WIDTH_PX = float(os.environ.get("BTT_LYRICS_MIN_WIDTH_PX", "160"))
MAX_LYRIC_WIDTH_PX = float(os.environ.get("BTT_LYRICS_MAX_WIDTH_PX", "400"))
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

# The Now Playing widget's own settings, mirrored from widgets/now-playing.sh
# (the script widget that replaced BTT's native Now Playing widget in
# bttpreset/Default.bttpreset). The script reads the same two format
# variables, so display and measurement share one knob. Line 1 is album
# first with the ▸ separator: for "What a Wonderful World" it reads
# "What a Wonderful World ▸ What a Wonderful World" at 13 pt.
NOW_PLAYING_LINE_FORMATS = (
    os.environ.get("BTT_LYRICS_NOW_PLAYING_LINE1", "{album} ▸ {title}"),
    os.environ.get("BTT_LYRICS_NOW_PLAYING_LINE2", "{artist}"),
)
# Mirrored from the shell widgets in bttpreset/Default.bttpreset. The Now
# Playing widget sits beside the Lyrics widget and now sets the same
# BTTTouchBarButtonFontSize, so the two read as one row rather than as a
# small caption next to a larger lyric.
LYRICS_FONT_SIZE = float(os.environ.get("BTT_LYRICS_FONT_SIZE", "13"))
NOW_PLAYING_FONT_SIZE = float(os.environ.get("BTT_LYRICS_NOW_PLAYING_FONT", "13"))
# BTT fits a two-row widget into the same fixed height by drawing the second
# row smaller than the first -- around 10-11 pt against a 13 pt first row
# (the same observation CONTINUATION_INDENT below is calibrated against).
# Both widgets on this part of the row set 13 pt, so they share this size
# too, and the second row of either one is measured with it.
SECOND_ROW_FONT_SIZE = float(os.environ.get("BTT_LYRICS_SECOND_ROW_FONT", "10.5"))
# How much Now Playing text the widget can actually show before BTT
# truncates it. Calibrated 2026-08-12 against the Saint-Saëns Organ
# Symphony: both rows were visible through "Poco a" (row 1) and "No. 3"
# (row 2), i.e. ~527 px of the 11 pt text -- far past the 400 pt box's
# nominal 365 pt text budget (BTTTBWidgetWidth 400 minus the 30 pt cover
# icon and 5 pt gap), so the box is not the limiter it looked like. The
# prediction caps at the observed visible extent; a longer title truncates
# rather than costing the lyric any more of the row. The cap is a width on
# the row, not a character count, so it survives the font size change above
# -- the same title simply reaches it sooner at 13 pt.
NOW_PLAYING_MAX_TEXT_PX = float(os.environ.get("BTT_LYRICS_NOW_PLAYING_MAX_PX", "527"))
# The OpenCode quota widget (widgets/opencode-quota.sh), the row's last
# item. While the Now Playing + Lyrics pair is short of space the lyrics
# widget hides it (BTT removes script widgets whose text is empty) and the
# pair's budget grows by the slot it frees: the widest of its two text
# rows at OPENCODE_FONT_SIZE, plus the icon (BTTTouchBarItemIconWidth 22),
# the gap after it (BTTTouchBarIconTextOffset 5), and the widget's own
# negative item padding and free space, all from bttpreset/Default.bttpreset.
OPENCODE_FONT_SIZE = float(os.environ.get("BTT_LYRICS_OPENCODE_FONT", "15"))
OPENCODE_ICON_PX = float(os.environ.get("BTT_LYRICS_OPENCODE_ICON_PX", "22"))
OPENCODE_ICON_OFFSET_PX = float(
    os.environ.get("BTT_LYRICS_OPENCODE_ICON_OFFSET_PX", "5")
)
OPENCODE_ITEM_PADDING_PX = float(os.environ.get("BTT_LYRICS_OPENCODE_PADDING_PX", "-5"))
OPENCODE_FREE_SPACE_PX = float(
    os.environ.get("BTT_LYRICS_OPENCODE_FREE_SPACE_PX", "-10")
)
# What the OpenCode widget shows before its first value, and the widest it
# can plausibly ever show -- the default the slot calculation falls back to.
OPENCODE_DEFAULT_TEXT = "100%\n7d"
# How long an untouched hide flag is believed: the lyrics widget rewrites it
# every tick while the pair is cramped, so an old flag belongs to a lyrics
# widget that stopped running, and OpenCode comes back.
OPENCODE_HIDE_MAX_AGE_SECONDS = float(
    os.environ.get("BTT_LYRICS_OPENCODE_HIDE_MAX_AGE", "90")
)
# The OpenCode widget's BetterTouchTool UUID, for the update_touch_bar_widget
# / refresh_widget kicks that hide and restore it. Mirrors
# actions/set-widget-variables.sh (BTT_WIDGET_OPENCODE_UUID).
OPENCODE_WIDGET_UUID = os.environ.get(
    "BTT_WIDGET_OPENCODE_UUID", "AE01C9E2-9EC6-4329-8358-8389BFB850F8"
)
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
# Row 2 carries no note symbol. Its line also renders at a smaller font than
# the first -- BTT fits both rows into the widget's fixed height by drawing
# the second around 10-11 pt, against the first row's 13 pt -- so spaces on
# it are proportionally narrower and the calibrated indent is 7 of them.
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
# The quota value the OpenCode widget is drawing (cache/opencode-quota.value
# lives beside the other widgets' values, not under cache/lyrics).
OPENCODE_VALUE_PATH = Path(
    os.environ.get(
        "BTT_LYRICS_OPENCODE_VALUE", str(REPO_DIR / "cache" / "opencode-quota.value")
    )
)
# Where the lyrics widget leaves the "pair is cramped" signal the OpenCode
# widget's own tick honours; see render.update_opencode_visibility.
OPENCODE_HIDE_PATH = CACHE_DIR / "opencode-hide"
# The cramped-pair hide is disabled for now: the lyrics widget leaves the
# OpenCode widget alone and never claims its slot. Set to True to hand the
# slot back to the pair while it is short of space (see
# render.update_opencode_visibility and viewport.effective_budget_px).
OPENCODE_HIDE_ENABLED = False
# What the last rendering tick put on screen, and when that frame is due to
# change. Written only by ticks that render something of their own -- see
# render.write_render_receipt -- and read back by render.last_rendered_key,
# which is how the next tick's fresh process knows what is already up there.
#
# It lives beside the trace rather than in CACHE_DIR because the freeze guard
# (retired 2026-08-12, see specs/CONSTRAINTS.md) read it from launchd, which
# gets EPERM opening anything under cache/. Nothing outside the widget reads
# it now; the path stays put because moving it would only strand the receipt
# a fresh widget process expects to find.
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
# The Lyrics and Now Playing widgets' BTT UUIDs, so the event watcher (the
# --sample helper and the --track-changed follow in cli.py) can run the
# closing sequence -- clear the lyric, then hide the row. Mirrors
# actions/set-widget-variables.sh, like OPENCODE_WIDGET_UUID above.
LYRICS_WIDGET_UUID = os.environ.get(
    "BTT_LYRICS_WIDGET_UUID", "E19BB023-5060-4A56-95C8-6E7402779870"
)
NOW_PLAYING_WIDGET_UUID = os.environ.get(
    "BTT_NOW_PLAYING_WIDGET_UUID", "710F54C5-25B0-4A2C-B960-D9C0FE78B1B7"
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
CACHE_KEY_VERSION = 6

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

# TEMPORARILY DISABLED (2026-08-11): each lookup copies Music's whole URL cache
# DB to a temp dir, and the render path re-runs it every second while a track
# is not_found. QQ Music/NetEase cover the tracks we care about. Flip back to
# True to restore the Apple TTML fast path.
APPLE_CACHE_ENABLED = False

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

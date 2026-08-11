#!/usr/bin/env zsh

#
# BetterTouchTool widget: the system Now Playing track, shown only while an
# allowed player holds it.
#
# The native BTT Now Playing widget follows whichever app owns the system's
# Now Playing session -- a browser playing YouTube included -- and BTT
# offers no per-app filter for it. This widget replaces it: it reads the
# same MediaRemote source the lyrics sampler reads, and prints the track
# only when the holder's bundle id is in the allowlist. Any other holder
# prints nothing, and BTT hides script widgets whose text is empty.
#
# The allowlist is an allowlist rather than a denylist on purpose: browsers
# are many and keep appearing, players are few and fixed (the Star widget
# gates on com.apple.Music for the same reason).
#
# Holding the session is not the same as being the player, though: a browser
# that starts a video takes Now Playing over while Apple Music keeps playing,
# and gating on the holder alone blanked the row for as long as the tab
# lived. So a holder outside the allowlist falls back to the Lyrics sampler's
# state, which reads Apple Music directly and therefore still names the real
# player -- see sampled_track below.
#
# Usage: now-playing.sh [widget-uuid]
# BTT: JSON {"text": "<album> ▸ <title>\n<artist>", "font_color": ...,
#            "icon_path": <album cover | play icon>}
# Terminal: plain "<album> ▸ <title>\n<artist>"
# Nothing when no allowed player holds Now Playing.
#

set -u
set -o pipefail
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

if [[ -n "${1:-}" ]]; then BTT_WIDGET_UUID="$1"; shift; else BTT_WIDGET_UUID="${BTT_WIDGET_UUID:-}"; fi

# Real players only, space-separated bundle ids. Browsers and anything else
# are excluded, so a YouTube tab never hijacks the row.
ALLOWED_BUNDLE_IDS="${BTT_NOW_PLAYING_ALLOWED:-com.apple.Music com.tencent.qqmusic}"

# The Lyrics widget beside this one, cleared before this widget redraws the
# row for a different track -- see clear_lyrics_for_change below. Defaulted
# rather than passed in, exactly as LYRICS_WIDGET_UUID is in
# widgets/lyrics/config.py, so the widget keeps working against a preset
# that has not been re-imported.
LYRICS_UUID="${BTT_LYRICS_WIDGET_UUID:-E19BB023-5060-4A56-95C8-6E7402779870}"

# The two rows, matching what the Lyrics widget measures its viewport
# against (BTT_LYRICS_NOW_PLAYING_LINE1/LINE2 in widgets/lyrics/config.py):
# the formats live in one place so display and measurement stay in step.
# With the stock pair the rows are balanced by rendered length -- the
# split with the smaller row-length delta of ({album} ▸ {title}, {artist})
# vs ({title}, {artist} ▸ {album}) -- and widgets/lyrics/viewport.py
# mirrors that choice so the measured width stays the drawn width.
# zsh brace expansion would mangle {album} inside a ${VAR:-default}, so the
# defaults are applied in the Python below instead.
LINE1_FMT="${BTT_LYRICS_NOW_PLAYING_LINE1:-}"
LINE2_FMT="${BTT_LYRICS_NOW_PLAYING_LINE2:-}"

CACHE_DIR="${BTT_WIDGET_CACHE_DIR:-${BTT_REPO_DIR:-$HOME/Documents/BTT}/cache}"
ASSETS_DIR="${BTT_REPO_DIR:-$HOME/Documents/BTT}/assets"
# The Lyrics sampler's state, consulted when the session holder is not one of
# ours. Mirrors CACHE_DIR / STATE_PATH in widgets/lyrics/config.py.
SAMPLER_STATE="${BTT_LYRICS_CACHE_DIR:-${BTT_REPO_DIR:-$HOME/Documents/BTT}/cache/lyrics}/state.json"
STATE_BIN="${BTT_NOW_PLAYING_STATE_BIN:-$HOME/Library/Application Support/BTT/nowplaying-state}"

raw_state() {
    # nowplaying-cli get-raw first: the compiled state helper (preferred by
    # the lyrics sampler for its true isPlaying flag) omits
    # kMRMediaRemoteNowPlayingInfoClientBundleIdentifier, and this widget's
    # allowlist gate cannot work without the holder's bundle id.
    # Subshell: zsh runs an EXIT trap set inside a function when the
    # function returns, after its locals are gone -- "$tmp" would be
    # unset in the trap. A subshell keeps plain variables alive until the
    # trap fires, and its exit status becomes the function's.
    (
        cli="$(command -v nowplaying-cli 2>/dev/null || true)"
        if [[ -x "$cli" ]]; then
            # nowplaying-cli can hang when no app holds a session; bound
            # the wait.
            tmp="$(mktemp)"
            trap 'rm -f "$tmp"' EXIT
            "$cli" get-raw >"$tmp" 2>/dev/null &
            pid=$!
            for _ in {1..30}; do
                kill -0 "$pid" 2>/dev/null || break
                sleep 0.05
            done
            kill "$pid" 2>/dev/null
            wait "$pid" 2>/dev/null
            cat "$tmp"
            exit 0
        fi
        # Last resort: the helper lacks the bundle id, so the gate fails
        # closed (nothing prints) -- degraded, but never a wrong player.
        [[ -x "$STATE_BIN" ]] && "$STATE_BIN" 2>/dev/null
    )
}

# An empty answer is not the end of it: MediaRemote goes silent while an app
# hands the session over, and the sampler fallback below may still have the
# track. The Python treats an unusable payload as an empty dictionary.
RAW="$(raw_state)" || RAW=""

python3 - "$RAW" "$ALLOWED_BUNDLE_IDS" "$LINE1_FMT" "$LINE2_FMT" \
    "$CACHE_DIR" "$BTT_WIDGET_UUID" "$ASSETS_DIR" "$LYRICS_UUID" \
    "$SAMPLER_STATE" <<'PY'
import base64
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path


def strip_parens(text):
    # "Song (feat. X)" -> "Song"; leftover double spaces collapse. The
    # Lyrics viewport strips identically (widgets/lyrics/viewport.py), so
    # the measured Now Playing width stays the width this widget draws.
    return re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", "", text)).strip()


def _png_chunk(tag, data):
    return (
        struct.pack(">I", len(data)) + tag + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def write_play_icon(path):
    """A small white play triangle on transparency, for the paused state.

    Pure stdlib (zlib/struct) so the widget needs no image tools; the icon
    is written once and reused. The triangle fills most of the canvas
    width; its tip stops short of the right edge so the gap to the text
    stays small when BTT scales the icon into the widget's icon slot. The
    proportions mirror assets/now-playing-play.svg.
    """
    width = height = 32
    raw = bytearray()
    # Triangle corners: A=(6,6) B=(28,16) C=(6,26); scanline fill.
    for y in range(height):
        raw.append(0)  # filter type 0
        for x in range(width):
            on = False
            if 6 <= y <= 26:
                right = 6 + 22 * (y - 6) / 10 if y <= 16 else 28 - 22 * (y - 16) / 10
                on = 6 <= x <= right
            raw += b"\xff\xff\xff\xff" if on else b"\x00\x00\x00\x00"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw)))
        + _png_chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def display_album(text):
    # Multi-work albums carry the second work after a semicolon, e.g.
    # "Mozart: Piano Concerto No. 23 K. 488; Piano Sonata K. 333" -- the
    # Touch Bar is too short for both, so only the first work is shown.
    return strip_parens(text).split(";")[0].strip()


def artwork_icon(info, cache_dir):
    """Album cover as a per-track icon file, or None.

    The raw dict carries artwork as base64 (nowplaying-cli get-raw). The
    file name is hashed from the bytes so BetterTouchTool sees a new path
    per track instead of a stale icon; old covers are removed.
    """
    encoded = info.get("kMRMediaRemoteNowPlayingInfoArtworkData") or ""
    if not encoded:
        return None
    try:
        data = base64.b64decode(encoded)
    except (ValueError, TypeError):
        return None
    magic = data[:4]
    ext = ".jpg" if magic[:3] == b"\xff\xd8\xff" else ".png" if magic == b"\x89PNG" else ""
    if not ext:
        return None
    name = f"now-playing-artwork-{hashlib.sha256(data).hexdigest()[:12]}{ext}"
    directory = Path(cache_dir)
    path = directory / name
    if not path.exists():
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            for old in directory.glob("now-playing-artwork-*"):
                if old.name != name:
                    old.unlink(missing_ok=True)
        except OSError:
            return None
    return str(path)


def play_icon(cache_dir, assets_dir):
    """The paused-state icon: the repo's SVG asset, or a generated PNG.

    BetterTouchTool renders SVG icon_path files, so the checked-in
    assets/now-playing-play.svg (a solid white triangle, matching the
    widget's small icon size) is preferred. If the asset is missing the
    triangle is generated once into the cache, keeping the widget
    self-contained.
    """
    svg = Path(assets_dir) / "now-playing-play.svg"
    if svg.is_file():
        return str(svg)
    directory = Path(cache_dir)
    path = directory / "now-playing-play.png"
    try:
        if not path.exists():
            directory.mkdir(parents=True, exist_ok=True)
            write_play_icon(path)
    except OSError:
        return None
    return str(path)


# How stale the sampler's state may be before this widget stops believing it.
# Mirrors STATE_MAX_AGE_SECONDS in widgets/lyrics/config.py, which is what the
# Lyrics widget itself allows before it treats a sample as gone.
SAMPLE_MAX_AGE_SECONDS = 8.0


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def sampled_track(state_path, allowed):
    """The real player's track, when MediaRemote's holder is not one of ours.

    A browser that starts a video takes over the system Now Playing session
    even while Apple Music keeps playing, so the holder's bundle id alone is
    not enough to decide the row is not ours -- gating on it blanked the
    widget for as long as the tab lived (the Star widget hides for the same
    reason; see actions/now-playing-app.sh).

    The Lyrics sampler does not have that blind spot: it reads Apple Music
    directly over AppleScript and only falls back to MediaRemote (sample_mode
    in widgets/lyrics/cli.py), so its state still names the real player.
    Reading its file costs a stat and a small JSON parse, which keeps the
    rule this widget is built around -- no AppleScript on the widget path,
    where every widget shares one script-runner service.

    Returns the sampled track, or None when the sample is missing, too old,
    or from a player this widget does not draw.
    """
    sample = read_json(Path(state_path))
    if not isinstance(sample, dict):
        return None
    try:
        age = time.time() - float(sample.get("sampled_at") or 0.0)
    except (TypeError, ValueError):
        return None
    if age > SAMPLE_MAX_AGE_SECONDS:
        return None

    track = sample.get("track")
    if not isinstance(track, dict):
        return None
    if track.get("state") not in {"playing", "paused"}:
        return None
    # The sampler names a source rather than a bundle id, and only
    # "apple_music" means a player it read for itself. Its "media_remote"
    # samples come from the very holder the caller just rejected, so
    # accepting them here would undo the allowlist.
    if track.get("source") != "apple_music":
        return None
    if "com.apple.Music" not in allowed.split():
        return None
    return track


def write_json(path, payload):
    """Write JSON so a concurrent reader sees the whole file or none of it.

    The Lyrics widget reads the marker written below on a one second tick of
    its own, and a torn read there would drop the hold it exists to keep.
    """
    temporary = path.with_name(f"{path.name}.{os.getpid()}")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass


def clear_lyrics_for_change(identity, cache_dir, lyrics_uuid):
    """Clear the Lyrics widget when this tick is about to change the row.

    This widget's text is what sets the pair's width, so a track change here
    is what makes the whole row reflow -- and the lyric beside it still
    belongs to the track leaving the screen. Clearing it first collapses the
    two visible jumps (an old lyric beside the new row, then the lyric
    returning at its own new width) into one, which is the flicker a track
    change used to show when the two widths differ a lot.

    The marker left behind is the one the Lyrics widget's own tick honours
    (CLEARED_MARKER_PATH in widgets/lyrics/config.py, read by
    render.cleared_while_sample_current), so its half-second sampler cannot
    undo the clear by repainting the old lyric a moment later.

    The Lyrics widget's watchers run the same sequence from the other side of
    the transition (cli.closing_sequence), because either side may notice
    first: this widget reads MediaRemote directly on BTT's tick, the sampler
    reads Music over AppleScript on its own. Both write the same marker and
    the clear is idempotent, so whichever gets there first simply wins.

    Fire-and-forget: an osascript launch costs ~200 ms, and every widget
    shares the single script-runner service this runs on.
    """
    path = Path(cache_dir) / "now-playing.identity"
    previous = read_json(path)
    if previous == identity:
        return

    # Only a track that was on screen leaves a stale lyric behind. Coming
    # back from an empty row (first run, or nothing was playing) has nothing
    # to clear, and a marker naming no track would match no sample anyway.
    if previous and previous.get("title") and lyrics_uuid:
        marker = dict(previous)
        marker["at"] = time.time()
        write_json(Path(cache_dir) / "lyrics-cleared", marker)
        try:
            subprocess.Popen(
                [
                    "/usr/bin/osascript",
                    "-e",
                    'tell application "BetterTouchTool" to '
                    f'update_touch_bar_widget "{lyrics_uuid}" text ""',
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            pass

    # Written last: a tick that died before the clear went out should try
    # again rather than record a change it never made.
    write_json(path, identity)


(
    payload,
    allowed,
    line1_fmt,
    line2_fmt,
    cache_dir,
    widget_uuid,
    assets_dir,
    lyrics_uuid,
    state_path,
) = sys.argv[1:10]
line1_fmt = line1_fmt or "{album} ▸ {title}"
line2_fmt = line2_fmt or "{artist}"
try:
    info = json.loads(payload)
except (json.JSONDecodeError, UnicodeDecodeError):
    info = {}
if not isinstance(info, dict):
    info = {}

# How the Lyrics widget's samplers name a track: the raw fields with their
# surrounding space trimmed and nothing else (widgets/lyrics/apple_music.py
# and media_remote.py). The marker written later has to name it the same
# way, or the Lyrics widget cannot match it against its own state.
bundle_id = info.get("kMRMediaRemoteNowPlayingInfoClientBundleIdentifier", "")
if bundle_id in allowed.split():
    identity = {
        field: str(info.get(key) or "").strip()
        for field, key in (
            ("title", "kMRMediaRemoteNowPlayingInfoTitle"),
            ("artist", "kMRMediaRemoteNowPlayingInfoArtist"),
            ("album", "kMRMediaRemoteNowPlayingInfoAlbum"),
        )
    }
    try:
        rate = float(info.get("kMRMediaRemoteNowPlayingInfoPlaybackRate") or 0.0)
    except (TypeError, ValueError):
        rate = 0.0
    playing = rate > 0
    # The album cover rides along in the same dictionary as the track.
    cover = artwork_icon(info, cache_dir)
else:
    # Somebody else holds the session -- a browser, most often. The sampler
    # still knows whether one of ours is playing behind it.
    track = sampled_track(state_path, allowed)
    if track is None:
        sys.exit(0)
    identity = {
        field: str(track.get(field) or "").strip()
        for field in ("title", "artist", "album")
    }
    playing = track.get("state") == "playing"
    # The artwork in the dictionary belongs to whoever holds the session, so
    # it is not this track's cover. Nothing else carries one without an
    # AppleScript call, so the row goes without an icon while it plays.
    cover = None

title = strip_parens(identity["title"])
artist = identity["artist"]
album = display_album(identity["album"])
if not title:
    sys.exit(0)

# A terminal run prints a row and touches nothing else.
if widget_uuid:
    clear_lyrics_for_change(identity, cache_dir, lyrics_uuid)

# The album cover is the icon while playing; a small play icon stands in
# while paused, matching the native widget's HideWhenPaused: 0 (still
# showing the track, but signalling it is not moving).
icon = cover if playing else play_icon(cache_dir, assets_dir)

fields = {"title": title, "artist": artist, "album": album}
# Balance the rows when the stock pair is in use: of ({album} ▸ {title},
# {artist}) and ({title}, {artist} ▸ {album}), keep the layout whose two
# rows are closer in rendered length (ties keep the stock order). The
# Lyrics viewport mirrors this (widgets/lyrics/viewport.py); custom
# BTT_LYRICS_NOW_PLAYING_* formats are used verbatim.
if line1_fmt == "{album} ▸ {title}" and line2_fmt == "{artist}" and artist:
    balanced = ("{title}", "{artist} ▸ {album}")
    delta_stock = abs(
        len(line1_fmt.format(**fields)) - len(line2_fmt.format(**fields))
    )
    delta_balanced = abs(
        len(balanced[0].format(**fields)) - len(balanced[1].format(**fields))
    )
    if delta_balanced < delta_stock:
        line1_fmt, line2_fmt = balanced
rows = [line1_fmt.format(**fields)]
if artist:
    rows.append(line2_fmt.format(**fields))
text = "\n".join(rows)
if not widget_uuid:
    print(text)
else:
    payload = {"text": text, "font_color": "255,255,255,255"}
    if icon:
        payload["icon_path"] = icon
    print(json.dumps(payload, ensure_ascii=False))
PY

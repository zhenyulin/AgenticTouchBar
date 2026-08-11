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

# The two rows, matching what the Lyrics widget measures its viewport
# against (BTT_LYRICS_NOW_PLAYING_LINE1/LINE2 in widgets/lyrics/config.py):
# the formats live in one place so display and measurement stay in step.
# zsh brace expansion would mangle {album} inside a ${VAR:-default}, so the
# defaults are applied in the Python below instead.
LINE1_FMT="${BTT_LYRICS_NOW_PLAYING_LINE1:-}"
LINE2_FMT="${BTT_LYRICS_NOW_PLAYING_LINE2:-}"

CACHE_DIR="${BTT_WIDGET_CACHE_DIR:-${BTT_REPO_DIR:-$HOME/Documents/BTT}/cache}"
ASSETS_DIR="${BTT_REPO_DIR:-$HOME/Documents/BTT}/assets"
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

RAW="$(raw_state)" || exit 0
[[ -n "$RAW" ]] || exit 0

python3 - "$RAW" "$ALLOWED_BUNDLE_IDS" "$LINE1_FMT" "$LINE2_FMT" \
    "$CACHE_DIR" "$BTT_WIDGET_UUID" "$ASSETS_DIR" <<'PY'
import base64
import hashlib
import json
import re
import struct
import sys
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
    is written once and reused. The triangle is deliberately small inside
    the 32 px canvas: BTT scales the icon into the widget's 30 px slot, and
    a full-size triangle dwarfed the 11 pt title.
    """
    width = height = 32
    raw = bytearray()
    # Triangle corners: A=(10,9) B=(10,23) C=(22,16); scanline fill.
    for y in range(height):
        raw.append(0)  # filter type 0
        for x in range(width):
            on = False
            if 9 <= y <= 23:
                right = 10 + 12 * (y - 9) / 7 if y <= 16 else 22 - 12 * (y - 16) / 7
                on = 10 <= x <= right
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


payload, allowed, line1_fmt, line2_fmt, cache_dir, widget_uuid, assets_dir = sys.argv[1:8]
line1_fmt = line1_fmt or "{album} ▸ {title}"
line2_fmt = line2_fmt or "{artist}"
try:
    info = json.loads(payload)
except (json.JSONDecodeError, UnicodeDecodeError):
    sys.exit(0)

bundle_id = info.get("kMRMediaRemoteNowPlayingInfoClientBundleIdentifier", "")
if bundle_id not in allowed.split():
    sys.exit(0)

title = strip_parens(str(info.get("kMRMediaRemoteNowPlayingInfoTitle") or ""))
artist = str(info.get("kMRMediaRemoteNowPlayingInfoArtist") or "").strip()
album = display_album(str(info.get("kMRMediaRemoteNowPlayingInfoAlbum") or ""))
if not title:
    sys.exit(0)

try:
    rate = float(info.get("kMRMediaRemoteNowPlayingInfoPlaybackRate") or 0.0)
except (TypeError, ValueError):
    rate = 0.0
# The album cover is the icon while playing; a small play icon stands in
# while paused, matching the native widget's HideWhenPaused: 0 (still
# showing the track, but signalling it is not moving).
icon = artwork_icon(info, cache_dir) if rate > 0 else play_icon(cache_dir, assets_dir)

fields = {"title": title, "artist": artist, "album": album}
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

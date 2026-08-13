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
# BTT: JSON {"text": "<title>\n<artist> ‣ <album>" or "<album> ‣ <title>\n<artist>",
#            whichever has the narrower widest row, "font_color": ...,
#            "icon_path": <album cover | player app icon | play icon>}
# Terminal: plain text of the same chosen layout
# Nothing when no allowed player holds Now Playing.
#

set -u
set -o pipefail
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

if [[ -n "${1:-}" ]]; then BTT_WIDGET_UUID="$1"; shift; else BTT_WIDGET_UUID="${BTT_WIDGET_UUID:-}"; fi

# Real players only, space-separated bundle ids. Browsers and anything else
# are excluded, so a YouTube tab never hijacks the row. Matched
# case-insensitively (see allowed_ids below), because these are spelled
# inconsistently in the wild -- QQ Music registers com.tencent.QQMusicMac.
ALLOWED_BUNDLE_IDS="${BTT_NOW_PLAYING_ALLOWED:-com.apple.Music com.tencent.QQMusicMac}"

# The Lyrics widget beside this one, cleared before this widget redraws the
# row for a different track -- see clear_lyrics_for_change below. Defaulted
# rather than passed in, exactly as LYRICS_WIDGET_UUID is in
# widgets/lyrics/config.py, so the widget keeps working against a preset
# that has not been re-imported.
LYRICS_UUID="${BTT_LYRICS_WIDGET_UUID:-E19BB023-5060-4A56-95C8-6E7402779870}"

CACHE_DIR="${BTT_WIDGET_CACHE_DIR:-${BTT_REPO_DIR:-$HOME/Documents/BTT}/cache}"
ASSETS_DIR="${BTT_REPO_DIR:-$HOME/Documents/BTT}/assets"
# The Lyrics sampler's state, consulted when the session holder is not one of
# ours. Mirrors CACHE_DIR / STATE_PATH in widgets/lyrics/config.py.
SAMPLER_STATE="${BTT_LYRICS_CACHE_DIR:-${BTT_REPO_DIR:-$HOME/Documents/BTT}/cache/lyrics}/state.json"

source "${0:A:h}/lib/media-remote.sh"

# The compiled state helper, for the one field nothing else has: isPlaying.
#
# kMRMediaRemoteNowPlayingInfoPlaybackRate is not a playback state. QQ Music
# keeps publishing rate 1 while paused and never republishes on a pause --
# measured here at 9 s of rate 1 after BTT's own Play or Pause action -- so
# the rate alone left the album cover on screen where the play icon belongs.
# specs/design/LYRICS.md records the same finding for the lyrics sampler,
# which is why this helper exists.
HELPER="$(media_remote_state)" || HELPER=""

# The dictionary the row is drawn from: the holder's bundle id for the
# allowlist gate, and the album artwork.
#
# The helper answers both since its 2026-08-13 rebuild, so ask it first and
# only fall back to nowplaying-cli when this session's holder did not publish
# them -- that call costs ~0.22 s against the helper's ~0.05 s, on a widget
# BetterTouchTool runs every second.
#
# Either way the dictionary is handed to Python as a path, not as text -- see
# lib/media-remote.sh for why. A call that produced nothing leaves the
# previous file in place: MediaRemote goes silent while an app hands the
# session over, and a stale dictionary a second old still names the right
# track. Python treats an unreadable one as empty, and the sampler fallback
# below may have the track anyway.
RAW_PATH="$CACHE_DIR/now-playing.raw.json"
if media_remote_state_is_complete "$HELPER"; then
    mkdir -p "$CACHE_DIR" 2>/dev/null
    print -r -- "$HELPER" > "$RAW_PATH.$$" 2>/dev/null &&
        mv -f "$RAW_PATH.$$" "$RAW_PATH" 2>/dev/null
else
    media_remote_raw "$RAW_PATH" || true
fi

python3 - "$RAW_PATH" "$ALLOWED_BUNDLE_IDS" "$CACHE_DIR" "$BTT_WIDGET_UUID" \
    "$ASSETS_DIR" "$LYRICS_UUID" \
    "$SAMPLER_STATE" "$HELPER" <<'PY'
# Imported here: what every tick needs. base64, hashlib, plistlib, struct,
# subprocess and zlib are imported by the functions that use them instead --
# measured at 47 ms for plistlib alone, 137 ms for the six together, on a
# widget BetterTouchTool runs every second through the one script-runner
# service every other widget is queued behind. None of the six is needed by a
# tick that draws a cover it has already cached.
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path


def allowed_ids(allowed):
    """The allowlist as a case-folded set.

    Bundle ids are compared case-insensitively because vendors spell their
    own inconsistently -- QQ Music registers com.tencent.QQMusicMac, which a
    literal comparison against a lower-case allowlist entry misses, and this
    gate fails closed: the row goes blank for a player it should be drawing.
    """
    return {item.lower() for item in allowed.split()}


def strip_parens(text):
    # "Song (feat. X)" -> "Song"; leftover double spaces collapse.
    return re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", "", text)).strip()


def _png_chunk(tag, data):
    import struct
    import zlib

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
    import struct
    import zlib

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


def display_width(text):
    """Estimated row width in layout cells: combining marks 0, CJK/wide 2,
    everything else 1.

    The same cell model the Lyrics widget measures with
    (widgets/lyrics/display/layout.py), so both widgets of the pair agree.
    """
    return sum(
        0
        if unicodedata.combining(ch)
        else 2
        if unicodedata.east_asian_width(ch) in {"W", "F", "A"}
        else 1
        for ch in text
    )


def join_parts(*parts):
    return " ▸ ".join(part for part in parts if part)


def artwork_icon(info, cache_dir):
    """Album cover as a per-track icon file, or None.

    The raw dict carries artwork as base64 (nowplaying-cli get-raw). The
    file name is hashed from the bytes so BetterTouchTool sees a new path
    per track instead of a stale icon; old covers are removed.

    The same cover arrives on every tick of a track, and decoding a few
    hundred KB of base64 and hashing the result to rediscover a file already
    on disk is the widget's largest avoidable cost after the imports. So a
    memo records which encoded payload produced which file, keyed by its
    length and CRC -- both cheap over the string as it arrived. Only a payload
    that does not match the memo is decoded, which in practice means only the
    first tick of each track.
    """
    encoded = info.get("kMRMediaRemoteNowPlayingInfoArtworkData") or ""
    if not encoded:
        return None

    import zlib

    directory = Path(cache_dir)
    memo_path = directory / "now-playing-artwork.memo"
    key = f"{len(encoded)}:{zlib.crc32(encoded.encode('ascii', 'ignore')) & 0xFFFFFFFF}"

    memo = read_json(memo_path)
    if isinstance(memo, dict) and memo.get("key") == key:
        cached = directory / str(memo.get("name") or "")
        if cached.is_file():
            return str(cached)

    import base64
    import hashlib

    try:
        data = base64.b64decode(encoded)
    except (ValueError, TypeError):
        return None
    magic = data[:4]
    ext = ".jpg" if magic[:3] == b"\xff\xd8\xff" else ".png" if magic == b"\x89PNG" else ""
    if not ext:
        return None
    name = f"now-playing-artwork-{hashlib.sha256(data).hexdigest()[:12]}{ext}"
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
    write_json(memo_path, {"key": key, "name": name})
    return str(path)


# The icon face to prefer out of an .icns: BTT scales it into a 30 px slot,
# so the smallest face at least this wide is already sharper than the slot.
PLAYER_ICON_MIN_PIXELS = 64
# How long a failed player-icon lookup is remembered before it is retried.
# Without it a player whose icon cannot be read (Spotlight off, so mdfind
# cannot place the app) would pay a subprocess on every one second tick.
# Minutes rather than hours because the same marker also absorbs a cold
# Spotlight query that simply ran past the bound below -- measured at 0.3 s
# warm, but slower first time -- and that one deserves a prompt retry.
PLAYER_ICON_MISS_SECONDS = 300.0
# Bound on the Spotlight query, matching the 1.5 s this widget already
# allows nowplaying-cli: it runs on BTT's one second tick, in the shell
# script runner every widget shares.
PLAYER_ICON_LOOKUP_TIMEOUT = 1.5
# Bundle ids are used in a Spotlight query and in a cache file name, so only
# the characters a bundle id is actually made of are accepted.
BUNDLE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def icns_png(data):
    """One embedded PNG out of .icns bytes, or None.

    Modern .icns files store each icon face as a whole PNG, so the face can
    be lifted out with struct alone -- no sips subprocess, keeping this off
    the widget path's cost budget. The smallest face at least
    PLAYER_ICON_MIN_PIXELS wide is preferred (the largest, when every face
    is smaller); the ancient uncompressed faces are skipped.
    """
    import struct

    if data[:4] != b"icns":
        return None
    (declared,) = struct.unpack(">I", data[4:8])
    end = min(len(data), declared)
    best = None
    offset = 8
    while offset + 8 <= end:
        tag, length = struct.unpack(">4sI", data[offset : offset + 8])
        if length < 8:
            break
        body = data[offset + 8 : offset + length]
        offset += length
        del tag
        # PNG signature, then the IHDR chunk whose width is bytes 16..20.
        if body[:8] != b"\x89PNG\r\n\x1a\n" or body[12:16] != b"IHDR":
            continue
        (width,) = struct.unpack(">I", body[16:20])
        # Smallest face >= the minimum wins; below the minimum, biggest wins.
        rank = (width < PLAYER_ICON_MIN_PIXELS, -width if width < PLAYER_ICON_MIN_PIXELS else width)
        if best is None or rank < best[0]:
            best = (rank, body)
    return best[1] if best else None


def app_icns(bundle_id):
    """The .icns file of an installed app, found by bundle id, or None."""
    import plistlib
    import subprocess

    try:
        found = subprocess.run(
            ["/usr/bin/mdfind", f"kMDItemCFBundleIdentifier == '{bundle_id}'"],
            capture_output=True,
            text=True,
            timeout=PLAYER_ICON_LOOKUP_TIMEOUT,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in found.splitlines():
        app = Path(line.strip())
        if not app.name.endswith(".app") or not app.is_dir():
            continue
        try:
            plist = plistlib.loads((app / "Contents/Info.plist").read_bytes())
            name = str(plist.get("CFBundleIconFile") or "AppIcon")
        except (OSError, ValueError, plistlib.InvalidFileException):
            name = "AppIcon"
        # CFBundleIconFile is written both with and without the extension.
        icns = app / "Contents/Resources" / (name if name.endswith(".icns") else f"{name}.icns")
        if icns.is_file():
            return icns
    return None


def player_icon(bundle_id, cache_dir):
    """The player app's own icon, standing in for a missing album cover.

    A track without artwork -- QQ Music serves some that way, and the
    sampler fallback below never has the cover to begin with -- used to draw
    a bare text row. The app icon says whose track it is instead, which is
    what the row is missing without a cover.

    Extracted once per player and cached: placing the app costs a Spotlight
    query, and this runs on BTT's one second tick. A player that updates its
    icon keeps the cached one until the cache file is removed, which is the
    right trade for an icon drawn at 30 px.
    """
    if not bundle_id or not BUNDLE_ID_PATTERN.fullmatch(bundle_id):
        return None
    directory = Path(cache_dir)
    path = directory / f"now-playing-player-{bundle_id}.png"
    if path.is_file():
        return str(path)

    # A lookup that just failed is not retried until the marker ages out.
    miss = directory / f"now-playing-player-{bundle_id}.miss"
    try:
        if time.time() - miss.stat().st_mtime < PLAYER_ICON_MISS_SECONDS:
            return None
    except OSError:
        pass

    import struct

    icns = app_icns(bundle_id)
    png = None
    if icns is not None:
        try:
            png = icns_png(icns.read_bytes())
        except (OSError, struct.error):
            png = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if png is None:
            miss.touch()
            return None
        path.write_bytes(png)
        miss.unlink(missing_ok=True)
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
    if "com.apple.music" not in allowed_ids(allowed):
        return None
    return track


def helper_playing(payload, title):
    """MediaRemote's own playing flag, from the state helper, or None.

    The playback rate cannot answer this: QQ Music publishes rate 1 while
    paused and does not republish when it pauses, so a tap on the widget
    stopped the music while the row kept its album cover. isPlaying is the
    flag Control Center's Now Playing tile draws, and it flips immediately.

    None when the helper is missing, unreadable, or describing a different
    track than the row -- the two readings are a moment apart, and a stale
    flag from the track before is worse than the rate this falls back to.
    """
    try:
        state = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(state, dict) or "isPlaying" not in state:
        return None
    seen = str(state.get("kMRMediaRemoteNowPlayingInfoTitle") or "").strip()
    if seen != title:
        return None
    return bool(state["isPlaying"])


def held_cover(cache_dir, identity):
    """The cover kept from an earlier tick of this same track, or None.

    QQ Music leaves the artwork out of the occasional payload while playing
    (measured: one tick in a few dozen). Without this the icon would drop to
    the player logo for that single tick and come back, which reads as a
    flicker; the cover on file still belongs to the track on screen, so the
    row holds it instead.
    """
    if read_json(Path(cache_dir) / "now-playing.identity") != identity:
        return None
    try:
        for path in sorted(Path(cache_dir).glob("now-playing-artwork-*")):
            return str(path)
    except OSError:
        pass
    return None


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
        import subprocess

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
    raw_path,
    allowed,
    cache_dir,
    widget_uuid,
    assets_dir,
    lyrics_uuid,
    state_path,
    helper_payload,
) = sys.argv[1:9]
# The dictionary is read from the file the shell half wrote, rather than
# taken from argv: it carries the album artwork as base64 -- see
# widgets/lib/media-remote.sh. A missing or unreadable one is an empty
# dictionary; the sampler fallback below may still have the track.
info = read_json(Path(raw_path))
if not isinstance(info, dict):
    info = {}

# How the Lyrics widget's samplers name a track: the raw fields with their
# surrounding space trimmed and nothing else (widgets/lyrics/apple_music.py
# and media_remote.py). The marker written later has to name it the same
# way, or the Lyrics widget cannot match it against its own state.
bundle_id = info.get("kMRMediaRemoteNowPlayingInfoClientBundleIdentifier", "")
if bundle_id.lower() in allowed_ids(allowed):
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
    # The rate only stands in where the helper cannot answer -- see
    # helper_playing for why it is not trusted on its own.
    flag = helper_playing(helper_payload, identity["title"])
    playing = rate > 0 if flag is None else flag
    # The album cover rides along in the same dictionary as the track.
    cover = artwork_icon(info, cache_dir) or held_cover(cache_dir, identity)
    player = bundle_id
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
    # it is not this track's cover, and nothing else carries one without an
    # AppleScript call. The player's own icon stands in below.
    cover = None
    player = "com.apple.Music"

title = strip_parens(identity["title"])
artist = identity["artist"]
album = display_album(identity["album"])
if not title:
    sys.exit(0)

# A terminal run prints a row and touches nothing else.
if widget_uuid:
    clear_lyrics_for_change(identity, cache_dir, lyrics_uuid)

# The album cover is the icon while playing, falling back to the player's
# own app icon when the track carries no artwork -- a bare text row leaves
# the reader nothing to place it by. A small play icon stands in while
# paused, matching the native widget's HideWhenPaused: 0 (still showing the
# track, but signalling it is not moving).
if playing:
    icon = cover or player_icon(player, cache_dir)
else:
    icon = play_icon(cache_dir, assets_dir) or player_icon(player, cache_dir)

fields = {"title": title, "artist": artist, "album": album}
# Album placement is a minimax over the two row layouts -- the layout whose
# wider row is narrower wins:
#   A: {title} over {artist} ‣ {album}
#   B: {album} ‣ {title} over {artist}
# Row widths are estimated in layout cells (display_width above); BTT's
# fixed 22 px two-row block renders row 1 at 13 px and row 2 at 9 px, and
# its own BTTTBWidgetWidth bounds each row.
layout_a = [title, join_parts(artist, album)]
layout_b = [join_parts(album, title), artist]
if max(display_width(row) for row in layout_a) <= max(
    display_width(row) for row in layout_b
):
    rows = layout_a
else:
    rows = layout_b
text = "\n".join(row for row in rows if row)
if not widget_uuid:
    print(text)
else:
    payload = {"text": text, "font_color": "255,255,255,255"}
    if icon:
        payload["icon_path"] = icon
    print(json.dumps(payload, ensure_ascii=False))
PY

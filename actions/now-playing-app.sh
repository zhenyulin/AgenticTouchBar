#!/usr/bin/env zsh

#
# BetterTouchTool helper: the bundle identifier of the system's current
# Now Playing source.
#
# The Star widget must hide as soon as another app (QQ Music, browsers, ...)
# takes over Now Playing, even when Apple Music is still running. BTT's
# BTTCurrentlyPlayingApp variable proved unreliable for that switch, so this
# resolves the holder from the same MediaRemote source the lyrics pipeline
# reads (nowplaying-cli get-raw). Prints nothing when no app holds Now
# Playing, or when the query fails.
#
# Holding the session is not the same as being the player, though: a browser
# that starts a video takes Now Playing over while Apple Music keeps playing,
# and reporting the browser then hid the Star widget (and sent the Open
# Player action to the wrong app) for as long as the tab lived. So a holder
# that is not Apple Music is checked against the Lyrics sampler's state,
# which reads Music directly and therefore still names the real player --
# the same fallback widgets/now-playing.sh makes, so the two agree on whose
# track the row is showing.
#
# Usage: now-playing-app.sh
# Prints: com.apple.Music | com.microsoft.edgemac | ... | (nothing)
#

set -u

PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# The Lyrics sampler's state, consulted below. Mirrors CACHE_DIR /
# STATE_PATH in widgets/lyrics/config.py.
SAMPLER_STATE="${BTT_LYRICS_CACHE_DIR:-${BTT_REPO_DIR:-$HOME/Documents/BTT}/cache/lyrics}/state.json"

# A missing nowplaying-cli, or an empty answer, is no longer the end of it:
# the sampler fallback below can still name the player.
OUTPUT=""
CLI="$(command -v nowplaying-cli 2>/dev/null || true)"
if [[ -x "$CLI" ]]; then
    # nowplaying-cli can hang when no app holds a session; run it in the
    # background and bound the wait at 1.5 s.
    tmp="$(mktemp)"
    trap 'rm -f "$tmp"' EXIT

    "$CLI" get-raw >"$tmp" 2>/dev/null &
    pid=$!
    for _ in {1..30}; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.05
    done
    kill "$pid" 2>/dev/null
    wait "$pid" 2>/dev/null

    OUTPUT="$(<"$tmp")"
fi

python3 -c '
import json
import sys
import time

# Mirrors STATE_MAX_AGE_SECONDS in widgets/lyrics/config.py: how stale the
# sampler state may be before it stops being believed.
SAMPLE_MAX_AGE_SECONDS = 8.0

try:
    info = json.load(sys.stdin)
except (json.JSONDecodeError, UnicodeDecodeError):
    info = {}
if not isinstance(info, dict):
    info = {}

holder = info.get("kMRMediaRemoteNowPlayingInfoClientBundleIdentifier", "")
if holder == "com.apple.Music":
    print(holder)
    sys.exit(0)

# Apple Music playing behind whoever holds the session is still the player
# the row is about -- see the note at the top of this script.
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        sample = json.load(handle)
    track = sample["track"]
    fresh = time.time() - float(sample["sampled_at"]) <= SAMPLE_MAX_AGE_SECONDS
except (OSError, ValueError, KeyError, TypeError):
    track, fresh = None, False

if (
    fresh
    and isinstance(track, dict)
    and track.get("source") == "apple_music"
    and track.get("state") in {"playing", "paused"}
):
    print("com.apple.Music")
    sys.exit(0)

print(holder)
' "$SAMPLER_STATE" <<<"$OUTPUT"

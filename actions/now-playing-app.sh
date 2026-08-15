#!/usr/bin/env zsh

#
# BetterTouchTool helper: the bundle identifier of the system's current
# Now Playing source.
#
# The Star widget must hide as soon as another app (QQ Music, browsers, ...)
# takes over Now Playing, even when Apple Music is still running. BTT's
# BTTCurrentlyPlayingApp variable proved unreliable for that switch, so this
# resolves the holder from the same MediaRemote source the lyrics pipeline
# reads. Prints nothing when no app holds Now Playing, or when the query
# fails.
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
# The dictionary comes from the shared cache the Now Playing widget refreshes
# every second (widgets/lib/media-remote.sh), so the Star widget's own gate
# costs a file read rather than another ~0.22 s nowplaying-cli call.
#
# Usage: now-playing-app.sh [--with-holder]
# Prints: com.apple.Music | com.microsoft.edgemac | ... | (nothing)
#
# With --with-holder, a second line follows: the raw session holder, before
# the fallback above rewrote it (empty when nobody holds the session). The two
# differ exactly when the player is not the app the media keys would reach,
# which is what actions/now-playing-toggle.sh has to know before it sends one.
# The default output is unchanged, because the Star widget and the Open Player
# trigger read it with `do shell script`, which returns every line.
#

set -u

PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

WITH_HOLDER=0
[[ "${1:-}" == "--with-holder" ]] && WITH_HOLDER=1

REPO_DIR="${BTT_REPO_DIR:-${0:A:h:h}}"
source "$REPO_DIR/widgets/lib/btt-widget.sh"
source "$REPO_DIR/widgets/lib/media-remote.sh"

# The Lyrics sampler's state, consulted below. Mirrors CACHE_DIR /
# STATE_PATH in widgets/lyrics/config.py.
SAMPLER_STATE="${BTT_LYRICS_CACHE_DIR:-${BTT_REPO_DIR:-$HOME/Documents/BTT}/cache/lyrics}/state.json"

RAW_PATH="$(media_remote_raw_shared 2)"

python3 -c '
import json
import sys
import time

raw_path, state_path, max_age, with_holder = sys.argv[1:5]

try:
    with open(raw_path, encoding="utf-8") as handle:
        info = json.load(handle)
except (OSError, ValueError):
    info = {}
if not isinstance(info, dict):
    info = {}

holder = info.get("kMRMediaRemoteNowPlayingInfoClientBundleIdentifier", "")


def emit(player):
    print(player)
    if with_holder == "1":
        print(holder)


if holder == "com.apple.Music":
    emit(holder)
    sys.exit(0)

# Apple Music playing behind whoever holds the session is still the player
# the row is about -- see the note at the top of this script.
try:
    with open(state_path, encoding="utf-8") as handle:
        sample = json.load(handle)
    track = sample["track"]
    fresh = time.time() - float(sample["sampled_at"]) <= float(max_age)
except (OSError, ValueError, KeyError, TypeError):
    track, fresh = None, False

if (
    fresh
    and isinstance(track, dict)
    and track.get("source") == "apple_music"
    and track.get("state") in {"playing", "paused"}
):
    emit("com.apple.Music")
    sys.exit(0)

emit(holder)
' "$RAW_PATH" "$SAMPLER_STATE" "$MEDIA_REMOTE_SAMPLE_MAX_AGE" "$WITH_HOLDER"

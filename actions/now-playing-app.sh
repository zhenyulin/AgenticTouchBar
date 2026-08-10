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
# Usage: now-playing-app.sh
# Prints: com.apple.Music | com.microsoft.edgemac | ... | (nothing)
#

set -u

PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

CLI="$(command -v nowplaying-cli 2>/dev/null || true)"
[[ -x "$CLI" ]] || exit 0

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
[[ -n "$OUTPUT" ]] || exit 0

python3 -c '
import json
import sys

try:
    info = json.load(sys.stdin)
except (json.JSONDecodeError, UnicodeDecodeError):
    sys.exit(0)
print(info.get("kMRMediaRemoteNowPlayingInfoClientBundleIdentifier", ""))
' <<<"$OUTPUT"

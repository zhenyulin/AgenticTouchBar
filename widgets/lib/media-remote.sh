#!/usr/bin/env zsh

#
# Shared MediaRemote access for the Now Playing widget and actions.
#
# Two sources, because neither alone answers the question:
#
#   nowplaying-cli get-raw    the raw Now Playing dictionary. The only source
#                             carrying the session holder's bundle id (which
#                             it decodes out of the ClientPropertiesData blob)
#                             and the album artwork.
#   nowplaying-state          the compiled helper (actions/nowplaying-state.m),
#                             for the one field nothing else has: isPlaying.
#                             The published playback rate lies -- QQ Music
#                             keeps reporting rate 1 while paused.
#
# This file exists because widgets/now-playing.sh and actions/now-playing-app.sh
# had the same bounded-call block copied between them.
#
# Two things are deliberate about how the call is made:
#
#   The wait is bounded. nowplaying-cli can hang when no app holds a session.
#
#   The answer goes to a file, not a variable. A track's artwork rides in the
#   same dictionary as base64 -- a few hundred KB for a typical cover -- and
#   routing that through a command substitution and then a child's argv copies
#   it twice for nothing, on a widget that runs every second. argv also has a
#   hard size limit that a large enough cover would eventually reach. The
#   reader is handed the path instead.
#

# zselect waits without forking /bin/sleep. The poll below runs up to 30 times
# per call, on the 1s widget: that was 30 potential forks a second.
zmodload zsh/zselect 2>/dev/null || true

# How long nowplaying-cli may take, in hundredths of a second.
MEDIA_REMOTE_TIMEOUT_CS="${BTT_NOW_PLAYING_CLI_TIMEOUT_CS:-150}"
MEDIA_REMOTE_POLL_CS=5

# Where the raw dictionary is shared between the Now Playing widget, the Star
# widget's gate, and the Lyrics sampler -- all three want the same answer, and
# the query costs ~0.22 s. Mirrors MEDIA_REMOTE_RAW_PATH in
# widgets/lyrics/config.py.
MEDIA_REMOTE_RAW_PATH="${BTT_NOW_PLAYING_RAW_PATH:-${BTT_WIDGET_CACHE_DIR:-${BTT_REPO_DIR:-$HOME/Documents/BTT}/cache}/now-playing.raw.json}"

# How stale the Lyrics sampler's state may be before these scripts stop
# believing it. One definition, shared by every consumer: it also lives in
# widgets/lyrics/config.py as STATE_MAX_AGE_SECONDS, which is what the Lyrics
# widget itself allows before it treats a sample as gone.
MEDIA_REMOTE_SAMPLE_MAX_AGE="${BTT_LYRICS_STATE_MAX_AGE:-8.0}"

# The compiled state helper, for isPlaying. Prints nothing when absent.
media_remote_state() {
    local bin="${BTT_NOW_PLAYING_STATE_BIN:-$HOME/Library/Application Support/BTT/nowplaying-state}"
    [[ -x "$bin" ]] && "$bin" 2>/dev/null
}

# Write the raw Now Playing dictionary to $1. Returns 1 when nowplaying-cli is
# missing or produced nothing, leaving any previous file untouched.
media_remote_raw() {
    local out="$1"
    # $commands is zsh's PATH hash: a lookup, where `command -v` in a command
    # substitution would be a fork. BTT_NOW_PLAYING_CLI overrides it, the way
    # BTT_NOW_PLAYING_STATE_BIN overrides the helper above -- these scripts set
    # their own PATH, so pointing at a different binary is the only way to
    # drive them from a fixture.
    local cli="${BTT_NOW_PLAYING_CLI:-${commands[nowplaying-cli]:-}}"
    [[ -n "$cli" && -x "$cli" ]] || return 1

    mkdir -p "${out:h}" 2>/dev/null || return 1

    local partial="$out.$$"
    "$cli" get-raw >"$partial" 2>/dev/null &
    local pid=$! waited=0

    while (( waited < MEDIA_REMOTE_TIMEOUT_CS )); do
        kill -0 "$pid" 2>/dev/null || break
        zselect -t "$MEDIA_REMOTE_POLL_CS" 2>/dev/null || /bin/sleep 0.05
        (( waited += MEDIA_REMOTE_POLL_CS ))
    done
    kill "$pid" 2>/dev/null
    wait "$pid" 2>/dev/null

    if [[ ! -s "$partial" ]]; then
        rm -f "$partial" 2>/dev/null
        return 1
    fi
    # Moved into place, so a concurrent reader never sees a half-written
    # dictionary -- the Lyrics widget reads this on a tick of its own.
    mv -f "$partial" "$out" 2>/dev/null || return 1
}

# The shared dictionary's path, refreshed first if it is older than $1 seconds.
#
# For a caller that only needs to know who holds the session: the Now Playing
# widget rewrites this every second, so most callers find it already current
# and pay nothing.
# Freshness comes from lib/btt-widget.sh when the caller has sourced it; a
# caller that has not simply refreshes every time, which is correct, only
# slower.
media_remote_raw_shared() {
    local max_age="${1:-2}"
    if (( ${+functions[btt__is_fresh]} )) &&
        btt__is_fresh "$MEDIA_REMOTE_RAW_PATH" "$max_age"; then
        printf '%s' "$MEDIA_REMOTE_RAW_PATH"
        return 0
    fi
    media_remote_raw "$MEDIA_REMOTE_RAW_PATH" || true
    printf '%s' "$MEDIA_REMOTE_RAW_PATH"
}

# Does a state-helper payload carry everything the Now Playing row needs?
#
# The helper reports isPlaying, which nothing else does, and since the
# 2026-08-13 rebuild it also decodes the two NSData values it used to drop:
# the holder's bundle id and the album artwork. When both are there it is the
# whole answer, and the ~0.22 s nowplaying-cli call can be skipped -- the
# helper costs ~0.05 s.
#
# Both are required, not either: the bundle id alone would leave the row
# drawing the player's logo where a cover belongs. Whether MediaRemote
# publishes them is up to the app holding the session, so this is a question
# asked per tick rather than a mode configured once.
media_remote_state_is_complete() {
    local payload="${1-}"
    [[ "$payload" == *'"kMRMediaRemoteNowPlayingInfoClientBundleIdentifier"'* \
       && "$payload" == *'"kMRMediaRemoteNowPlayingInfoArtworkData"'* ]]
}

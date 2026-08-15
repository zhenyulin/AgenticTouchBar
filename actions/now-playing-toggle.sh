#!/usr/bin/env zsh

#
# BetterTouchTool action: play or pause the player the Now Playing row is
# showing -- not whoever holds the system session.
#
# BTT's own "Play or Pause" action sends the media key, and macOS routes that
# to the app holding the Now Playing session. A browser playing a video takes
# the session over while Apple Music keeps playing, so a tap on the row paused
# the video and left the row's own track running. The widget had already
# learned to ignore browsers (widgets/now-playing.sh); its tap had not, which
# is the whole reason this script exists.
#
# The player is resolved the way the row resolves it --
# actions/now-playing-app.sh, which prefers an allowlisted session holder and
# falls back to the Lyrics sampler when a browser holds the session -- and is
# then addressed directly:
#
#   com.apple.Music     AppleScript playpause, which reaches Music whether or
#                       not it holds the session. This is the case the media
#                       key got wrong.
#   another allowed     the media key, through BTT, but only while that player
#   player              still holds the session -- which is why the holder is
#                       asked for alongside the player. QQ Music ships no
#                       scripting dictionary to address instead, so the key is
#                       the only way to reach it, and a key sent while somebody
#                       else holds the session is the very bug above. Today the
#                       resolver never names a non-Music player it did not read
#                       as the holder; the guard is what keeps that true if its
#                       fallback is ever widened the way the widget's was.
#   anything else       nothing. The row is not ours, so neither is the tap.
#
# The player is asked for synchronously -- bounded, and nearly free while the
# widget's shared dictionary is current -- but the toggle itself is detached:
# this runs in the one shell script runner service every widget shares, and an
# AppleScript call into an unresponsive Music has frozen that runner for
# minutes before (see widgets/lib/btt-widget.sh).
#
# Usage: now-playing-toggle.sh
#

set -u

PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

REPO_DIR="${BTT_REPO_DIR:-${0:A:h:h}}"
source "$REPO_DIR/widgets/lib/btt-widget.sh"

# Whose tap this acts on, matched case-folded. The same knob and the same
# default as widgets/now-playing.sh, so the row and its tap cannot disagree
# about who counts as a player.
ALLOWED_BUNDLE_IDS="${BTT_NOW_PLAYING_ALLOWED:-com.apple.Music com.tencent.QQMusicMac}"

resolved=("${(@f)$("$REPO_DIR/actions/now-playing-app.sh" --with-holder)}")
player="${resolved[1]:-}"
holder="${resolved[2]:-}"
[[ -n "$player" ]] || exit 0

allowed=0
for id in ${=ALLOWED_BUNDLE_IDS}; do
    if [[ "${id:l}" == "${player:l}" ]]; then
        allowed=1
        break
    fi
done
(( allowed )) || exit 0

if [[ "${player:l}" == "com.apple.music" ]]; then
    # Guarded on running, so a tap can never launch Music by itself.
    btt_spawn_detached /usr/bin/osascript -e \
        'if application "Music" is running then tell application "Music" to playpause'
elif [[ "${player:l}" == "${holder:l}" ]]; then
    # 23 is BTT's own "Play or Pause" -- the media key, which macOS delivers
    # to the session holder, which this player is.
    btt_spawn_detached /usr/bin/osascript -e \
        'tell application "BetterTouchTool" to trigger_action "{\"BTTPredefinedActionType\":23}"'
fi

exit 0

#!/usr/bin/env zsh
#
# BetterTouchTool action: the track just changed, so catch the lyrics up.
#
# Usage:
#   track-changed.sh [lyrics-widget-uuid]
#
# Attach this as a second action on the swipe triggers that send Next and
# Previous. BTT's own media-key action changes the track; this one makes the
# Lyrics widget follow it immediately instead of at its next tick.
#
# Nothing is done here directly. BTT runs shell actions through the same
# single BetterTouchToolShellScriptRunner XPC service as the widgets, so a
# second spent waiting in this script is a second in which no widget on the
# Touch Bar updates and no tap-refresh is answered. The work is handed to a
# detached process and this returns at once.
#

set -u

PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

UUID="${1:-${BTT_LYRICS_WIDGET_UUID:-}}"

# Detached, and with every stream closed: a background child that still holds
# this script's stdout keeps the pipe open, and BTT waits on that pipe -- which
# would reintroduce exactly the blocking this file exists to avoid.
nohup /usr/bin/env python3 \
    "$HOME/Documents/BTT/now_playing_lyrics.py" --track-changed "$UUID" \
    </dev/null >/dev/null 2>&1 &

disown 2>/dev/null || true

exit 0

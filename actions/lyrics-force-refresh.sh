#!/usr/bin/env zsh
#
# BetterTouchTool action: tap-to-refresh for the Lyrics widget.
#
# Usage:
#   lyrics-force-refresh.sh [lyrics-widget-uuid]
#
# Attach this as the first action on the Lyrics widget. It drops the cached
# lyrics for the currently playing track. BTT's following Real JavaScript
# action repaints the widget after this short-lived external operation, so
# this helper does not call BetterTouchTool back through AppleScript.
#
# Nothing is done here directly. BTT runs shell actions through the same
# single BetterTouchToolShellScriptRunner XPC service as the widgets, so a
# second spent waiting in this script is a second in which no widget on the
# Touch Bar updates and no other tap-refresh is answered. The work is handed
# to a detached process and this returns at once.
#

set -u

PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

UUID="${1:-${BTT_LYRICS_WIDGET_UUID:-}}"

# Detached, and with every stream closed: a background child that still holds
# this script's stdout keeps the pipe open, and BTT waits on that pipe -- which
# would reintroduce exactly the blocking this file exists to avoid.
nohup /usr/bin/env python3 \
    "$HOME/Documents/BTT/widgets/lyrics/now_playing_lyrics.py" --clear-current \
    </dev/null >/dev/null 2>&1 &

disown 2>/dev/null || true

exit 0

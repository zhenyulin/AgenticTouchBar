#!/usr/bin/env zsh
#
# BetterTouchTool action: tap-to-refresh for the Lyrics widget.
#
# Usage:
#   lyrics-force-refresh.sh [lyrics-widget-uuid]
#
# Attach this as the tap action on the Lyrics widget. It drops the cached
# lyrics for whatever track is currently known, asks for a fresh Apple Music
# sample, and asks BTT to repaint -- the same "clear it and let the next tick
# refetch" idea as clear_current_cache(), but reading the last sample instead
# of querying Music directly, since a query can stall for seconds.
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
    "$HOME/Documents/BTT/widgets/lyrics/now_playing_lyrics.py" --force-refresh "$UUID" \
    </dev/null >/dev/null 2>&1 &

disown 2>/dev/null || true

exit 0

#!/usr/bin/env zsh

#
# BetterTouchTool action: the track just changed.
#
# Usage:
#   track-changed.sh [lyrics-widget-uuid]
#
# Environment fallbacks:
#   BTT_LYRICS_WIDGET_UUID
#

set -u

PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

LYRICS_UUID="${1:-${BTT_LYRICS_WIDGET_UUID:-}}"

#
# Lyrics
#
# Hand the relatively expensive lyrics work to a completely detached process
# so BTT's shell-script runner is released immediately.
#

if [[ -n "$LYRICS_UUID" ]]; then
    nohup /usr/bin/env python3 \
        "$HOME/Documents/BTT/widgets/lyrics/now_playing_lyrics.py" \
        --track-changed "$LYRICS_UUID" \
        </dev/null >/dev/null 2>&1 &
fi

disown 2>/dev/null || true

exit 0

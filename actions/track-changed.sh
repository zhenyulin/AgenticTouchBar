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

source "$HOME/Documents/BTT/widgets/lib/btt-widget.sh"

LYRICS_UUID="${1:-${BTT_LYRICS_WIDGET_UUID:-}}"

#
# Lyrics
#
# Hand the relatively expensive lyrics work to a completely detached process
# so BTT's shell-script runner is released immediately. This follows Music.app
# via AppleScript for a few seconds (see track_changed_mode in
# widgets/lyrics/cli.py), so it must run in its own session -- see
# btt_spawn_detached in widgets/lib/btt-widget.sh for why a plain
# `nohup … &` is not enough.
#

if [[ -n "$LYRICS_UUID" ]]; then
    btt_spawn_detached "$HOME/Documents/BTT/widgets/now-playing-lyrics.sh" \
        --track-changed "$LYRICS_UUID"
fi

exit 0

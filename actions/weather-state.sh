#!/usr/bin/env zsh
#
# BetterTouchTool action: record the playback state a Now Playing tap just
# set, so the weather widgets can flip instantly instead of waiting for the
# detached sampler's hold window (cache/lyrics/state.json can still report
# the previous state for a few seconds after a pause or play).
#
# Usage:
#   weather-state.sh <playing|paused>
#

set -u
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

STATE="${1:-}"
if [[ "$STATE" != "playing" && "$STATE" != "paused" ]]; then
    exit 2
fi

SELF="${0:A}"
source "${SELF:h}/../widgets/lib/btt-widget.sh"

btt_cache_put weather-tap-state "$STATE"

#!/usr/bin/env zsh
#
# BetterTouchTool TEST widget.
#
# Purpose: a widget with no network or Apple Music dependency, so it can be
# used as a control when diagnosing the tap-refresh/freeze issue tracked in
# actions/btt-freeze-guard.sh.
#
#   1. Tap-refresh check: tapping this widget's Touch Bar button should grey
#      it out for REFRESH_DELAY seconds (see widgets/lib/btt-widget.sh's
#      lock-based coloring), then redraw white with a new timestamp. If that
#      doesn't happen, the tap-refresh plumbing itself is broken -- nothing
#      about the lyrics widget's own network or Apple Music calls is involved.
#   2. Freeze check: disable every other widget in BTT, leave this one
#      running, and watch `widgets/now-playing-lyrics.sh --report`. Every
#      ordinary tick writes a trace row (see btt_trace below); a gap there
#      with only this widget enabled means BTT's script runner itself is
#      wedging, not any particular widget's slow work.
#

set -u
set -o pipefail
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

REFRESH_MODE=0
if [[ "${1:-}" == "--refresh" ]]; then REFRESH_MODE=1; shift; fi
if [[ -n "${1:-}" ]]; then BTT_WIDGET_UUID="$1"; shift; else BTT_WIDGET_UUID="${BTT_WIDGET_UUID:-}"; fi

SELF="${0:A}"
source "${SELF:h}/lib/btt-widget.sh"

STARTED="$(btt_now)"
BTT_WIDGET_NAME="test-widget"
BTT_WIDGET_REFRESH_MAX_RUN=30

# Effectively infinite: this is independent of the tick interval (a faster
# tick just writes trace.tsv rows more often, it doesn't age the value
# faster), and staleness-based auto-refresh isn't wanted here at all -- the
# only triggers should be a tap (via btt_force_pending) and the very first
# run, where btt_cache_get returns non-zero simply because no value exists
# yet, not because one aged out.
VALUE_MAX_AGE="${TEST_WIDGET_MAX_AGE:-999999999}"

# How long the simulated refresh takes, i.e. how long the grey period lasts.
# Kept visible (a few seconds) rather than instant, so the color change is
# actually observable.
REFRESH_DELAY="${TEST_WIDGET_REFRESH_DELAY:-2}"

if (( REFRESH_MODE )); then
    sleep "$REFRESH_DELAY"
    REFRESHED="$(date '+%H:%M:%S')"
    btt_cache_put "$BTT_WIDGET_NAME" "$REFRESHED"
    btt_trace refresh "$STARTED" ok "value=$REFRESHED"
    exit 0
fi

VALUE="$(btt_cache_get "$BTT_WIDGET_NAME" "$VALUE_MAX_AGE")"
FRESH=$?
FORCE=0; btt_force_pending && FORCE=1
if (( FRESH != 0 || FORCE )); then
    btt_refresh_detached "$BTT_WIDGET_NAME" "$BTT_WIDGET_REFRESH_MAX_RUN" "$SELF" --refresh "$BTT_WIDGET_UUID"
fi
OUTCOME=cached; (( FRESH != 0 )) && OUTCOME=stale; (( FORCE )) && OUTCOME=forced; [[ -n "$VALUE" ]] || OUTCOME=empty
btt_trace widget "$STARTED" "$OUTCOME"
btt_publish "TEST ${VALUE:-…}"

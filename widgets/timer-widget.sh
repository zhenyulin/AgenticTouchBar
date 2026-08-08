#!/usr/bin/env zsh
#
# BetterTouchTool timer widget.
#
# Purpose: a widget with no network or Apple Music dependency that shows how
# long the current BetterTouchTool process has been alive. If this value stops
# advancing while another widget still changes after a track switch, the
# track-change action is reaching the lyrics path but BTT is not dispatching
# ordinary widget ticks or tap refreshes reliably.
#
#   1. Tap-refresh check: tapping this widget's Touch Bar button should grey
#      it out for REFRESH_DELAY seconds (see widgets/lib/btt-widget.sh's
#      lock-based coloring), then redraw dark with the same advancing timer.
#      If that
#      doesn't happen, the tap-refresh plumbing itself is broken -- nothing
#      about the lyrics widget's own network or Apple Music calls is involved.
#   2. Freeze check: disable every other widget in BTT, leave this one
#      running, and watch `widgets/now-playing-lyrics.sh --report`. Every
#      ordinary tick writes a trace row with the timer value; a gap there with
#      only this widget enabled means BTT's script runner itself is wedging,
#      not any particular widget's slow work.
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
BTT_WIDGET_NAME="timer-widget"
BTT_WIDGET_REFRESH_MAX_RUN=30
# Keep the idle timer soft gray; the shared refresh color remains darker so a
# tap has a subtle light-up effect without returning all the way to white.
BTT_WIDGET_COLOR="175,175,175,255"

# How long the simulated refresh takes, i.e. how long the grey period lasts.
# Kept visible (a few seconds) rather than instant, so the color change is
# actually observable.
REFRESH_DELAY="${TIMER_WIDGET_REFRESH_DELAY:-2}"

# Persist the start time for the current BTT PID. This resets on a BTT
# restart, while a frozen BTT naturally leaves the displayed timer unchanged.
BTT_PID="$(/usr/bin/pgrep -x BetterTouchTool 2>/dev/null | /usr/bin/sed -n '1p')"
PID_VALUE="$(btt_cache_get timer-widget.btt-pid 999999999 2>/dev/null || true)"
START_VALUE="$(btt_cache_get timer-widget.started-at 999999999 2>/dev/null || true)"
if [[ -n "$BTT_PID" && "$BTT_PID" != "$PID_VALUE" ]] || [[ ! "$START_VALUE" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    START_VALUE="$STARTED"
    [[ -n "$BTT_PID" ]] && btt_cache_put timer-widget.btt-pid "$BTT_PID"
    btt_cache_put timer-widget.started-at "$START_VALUE"
fi

ELAPSED_SECONDS=$(( STARTED - START_VALUE ))
(( ELAPSED_SECONDS < 0 )) && ELAPSED_SECONDS=0
MINUTES=$(( ELAPSED_SECONDS / 60 ))
SECONDS=$(( ELAPSED_SECONDS % 60 ))
TIMER_VALUE="$(printf '%02d:%02d' "$MINUTES" "$SECONDS")"

if (( REFRESH_MODE )); then
    sleep "$REFRESH_DELAY"
    btt_cache_put "$BTT_WIDGET_NAME" "$TIMER_VALUE"
    btt_trace refresh "$STARTED" ok "timer=$TIMER_VALUE"
    exit 0
fi

VALUE="$TIMER_VALUE"
FORCE=0; btt_force_pending && FORCE=1
if (( FORCE )); then
    btt_refresh_detached "$BTT_WIDGET_NAME" "$BTT_WIDGET_REFRESH_MAX_RUN" "$SELF" --refresh "$BTT_WIDGET_UUID"
fi
OUTCOME=cached; (( FORCE )) && OUTCOME=forced
btt_trace widget "$STARTED" "$OUTCOME" "timer=$TIMER_VALUE btt_pid=${BTT_PID:-unknown}"
btt_publish "${VALUE}"

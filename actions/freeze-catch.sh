#!/usr/bin/env zsh
#
# Live capture for the tap-refresh/freeze issue (see actions/btt-freeze-guard.sh
# and widgets/timer-widget.sh). Retroactive `log show` mining after a freeze has
# already ended turns up nothing conclusive -- by the time you look, the
# evidence is gone. This instead watches the shared widget trace in real time
# and, the moment it goes quiet for too long, runs `sample` against BTT's main
# process and its BetterTouchToolShellScriptRunner XPC helper WHILE they are
# still stuck. That gives an actual stack trace of what the wedge is blocked
# on, instead of another gap in a log file.
#
# Run this in a terminal and leave it -- it is a one-off diagnostic, not a
# background service:
#
#   ./actions/freeze-catch.sh
#
# The timer probe actively asks BTT to refresh the timer widget and waits for
# a newer timer trace row. A failed probe is stronger evidence than passive
# trace silence because it tests the same refresh path used by tap-refresh.
#
# Usage:
#   FREEZE_CATCH_TIMER_TIMEOUT=5 ./actions/freeze-catch.sh
#   FREEZE_CATCH_POLL=10         ./actions/freeze-catch.sh
#

set -u
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

REPO_DIR="${BTT_REPO_DIR:-${0:A:h:h}}"
CACHE_DIR="${BTT_WIDGET_CACHE_DIR:-$REPO_DIR/cache}"
LOG_DIR="${BTT_LOG_DIR:-$REPO_DIR/logs}"
TRACE="${BTT_WIDGET_TRACE_FILE:-$LOG_DIR/trace.tsv}"
LOG="$LOG_DIR/freeze-catch.log"
OUT_DIR="$LOG_DIR/freeze-samples"
POLL="${FREEZE_CATCH_POLL:-10}"
TIMER_WIDGET_UUID="E25C395A-FE13-4216-BC59-6317FD0454BF"
TIMER_REFRESH_TIMEOUT="${FREEZE_CATCH_TIMER_TIMEOUT:-5}"
TIMER_REFRESH_POLL="${FREEZE_CATCH_TIMER_POLL:-1}"
SAMPLE_SECONDS="${FREEZE_CATCH_SAMPLE_SECONDS:-3}"

mkdir -p "$OUT_DIR" "$LOG_DIR"

log() {
    printf '%s %s\n' "$(/bin/date '+%Y-%m-%d %H:%M:%S')" "$*" | /usr/bin/tee -a "$LOG"
}

timer_trace_stamp() {
    /usr/bin/awk -F '\t' '$2 == "timer-widget" && $3 == "widget" { latest = $1 } END { printf "%.6f\n", latest + 0 }' "$TRACE" 2>/dev/null || printf '0\n'
}

timer_trace_row() {
    /usr/bin/awk -F '\t' '$2 == "timer-widget" && $3 == "widget" { latest = $0 } END { print latest }' "$TRACE" 2>/dev/null
}

timer_widget_responsive() {
    local before after probe_pid elapsed
    before="$(timer_trace_stamp)"
    /usr/bin/osascript -e "tell application \"BetterTouchTool\" to refresh_widget \"$TIMER_WIDGET_UUID\"" >/dev/null 2>&1 &
    probe_pid=$!

    for (( elapsed = 0; elapsed <= TIMER_REFRESH_TIMEOUT; elapsed++ )); do
        after="$(timer_trace_stamp)"
        if (( after > before )); then
            kill "$probe_pid" >/dev/null 2>&1 || true
            LAST_TIMER_TRACE="$(timer_trace_row)"
            return 0
        fi
        (( elapsed < TIMER_REFRESH_TIMEOUT )) && sleep "$TIMER_REFRESH_POLL"
    done

    kill "$probe_pid" >/dev/null 2>&1 || true
    LAST_TIMER_TRACE="$(timer_trace_row)"
    return 1
}

capture() {
    local stall_started="$1"
    local stamp
    stamp="$(/bin/date '+%Y%m%d-%H%M%S')"
    local bundle="$OUT_DIR/$stamp"
    mkdir -p "$bundle"

    log "capturing stall evidence to $bundle"

    {
        echo "stall detected at: $(/bin/date -r "$stall_started" '+%Y-%m-%d %H:%M:%S')"
        echo "capture started at: $(/bin/date '+%Y-%m-%d %H:%M:%S')"
        echo "trace file: $TRACE"
        echo "final timer trace: ${LAST_TIMER_TRACE:-none}"
        echo
        echo "=== ps: BetterTouchTool processes ==="
        /bin/ps aux | /usr/bin/grep -i bettertouchtool | /usr/bin/grep -v grep
        echo
        echo "=== last 20 trace.tsv rows before the stall ==="
        /usr/bin/tail -20 "$TRACE" 2>/dev/null
    } > "$bundle/context.txt"

    # Both run in parallel: the whole point is to catch them stuck at the same
    # moment, and each `sample` call itself blocks for SAMPLE_SECONDS.
    /usr/bin/sample BetterTouchTool "$SAMPLE_SECONDS" -file "$bundle/BetterTouchTool.sample.txt" >/dev/null 2>&1 &
    /usr/bin/sample BetterTouchToolShellScriptRunner "$SAMPLE_SECONDS" -file "$bundle/ShellScriptRunner.sample.txt" >/dev/null 2>&1 &
    wait

    log "capture complete: $bundle"
}

log "probing timer-widget via $TRACE"
log "timeout=${TIMER_REFRESH_TIMEOUT}s interval=${POLL}s sample=${SAMPLE_SECONDS}s captures=$OUT_DIR"
log "Ctrl-C to stop"

CAPTURED_THIS_STALL=0
STALL_STARTED_AT=0

while true; do
    CHECK_STARTED_AT="$(/bin/date +%s)"
    if timer_widget_responsive; then
        log "timer-widget responsive trace=${LAST_TIMER_TRACE:-none}"
        if (( CAPTURED_THIS_STALL )); then
            log "timer-widget responded after $(( CHECK_STARTED_AT - STALL_STARTED_AT ))s"
        fi
        CAPTURED_THIS_STALL=0
    elif (( ! CAPTURED_THIS_STALL )); then
        STALL_STARTED_AT="$CHECK_STARTED_AT"
        log "timer-widget unresponsive for ${TIMER_REFRESH_TIMEOUT}s -- final_trace=${LAST_TIMER_TRACE:-none}"
        capture "$STALL_STARTED_AT"
        CAPTURED_THIS_STALL=1
    fi

    CHECK_FINISHED_AT="$(/bin/date +%s)"
    SLEEP_SECONDS=$(( POLL - (CHECK_FINISHED_AT - CHECK_STARTED_AT) ))
    (( SLEEP_SECONDS > 0 )) && sleep "$SLEEP_SECONDS"
done

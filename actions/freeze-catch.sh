#!/usr/bin/env zsh
#
# Live capture for the tap-refresh/freeze issue (see actions/btt-freeze-guard.sh
# and widgets/test-widget.sh). Retroactive `log show` mining after a freeze has
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
# THRESHOLD must exceed the widget tick interval you've configured in BTT
# (currently 60s) or every ordinary gap between ticks will look like a stall.
#
# Usage:
#   FREEZE_CATCH_THRESHOLD=90   ./actions/freeze-catch.sh   # seconds of silence -> capture
#   FREEZE_CATCH_POLL=3         ./actions/freeze-catch.sh   # how often to check
#

set -u
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

CACHE_DIR="${BTT_WIDGET_CACHE_DIR:-$HOME/Library/Caches/btt-widgets}"
TRACE="$CACHE_DIR/trace.tsv"
OUT_DIR="$CACHE_DIR/freeze-samples"
THRESHOLD="${FREEZE_CATCH_THRESHOLD:-90}"
POLL="${FREEZE_CATCH_POLL:-3}"
SAMPLE_SECONDS="${FREEZE_CATCH_SAMPLE_SECONDS:-3}"

mkdir -p "$OUT_DIR"

trace_mtime() {
    /usr/bin/stat -f%m "$TRACE" 2>/dev/null || printf '0'
}

capture() {
    local stall_started="$1"
    local stamp
    stamp="$(/bin/date '+%Y%m%d-%H%M%S')"
    local bundle="$OUT_DIR/$stamp"
    mkdir -p "$bundle"

    echo "  -> capturing to $bundle"

    {
        echo "stall detected at: $(/bin/date -r "$stall_started" '+%Y-%m-%d %H:%M:%S')"
        echo "capture started at: $(/bin/date '+%Y-%m-%d %H:%M:%S')"
        echo "trace file: $TRACE"
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

    echo "  -> done: $bundle"
}

echo "watching $TRACE"
echo "threshold=${THRESHOLD}s poll=${POLL}s sample=${SAMPLE_SECONDS}s -> $OUT_DIR"
echo "(Ctrl-C to stop)"

LAST_SEEN="$(trace_mtime)"
LAST_CHANGE_AT="$(/bin/date +%s)"
CAPTURED_THIS_STALL=0

while true; do
    sleep "$POLL"

    NOW="$(/bin/date +%s)"
    CURRENT="$(trace_mtime)"

    if [[ "$CURRENT" != "$LAST_SEEN" ]]; then
        if (( CAPTURED_THIS_STALL )); then
            echo "$(/bin/date '+%H:%M:%S') resumed after $(( NOW - LAST_CHANGE_AT ))s of silence"
        fi
        LAST_SEEN="$CURRENT"
        LAST_CHANGE_AT="$NOW"
        CAPTURED_THIS_STALL=0
        continue
    fi

    SILENT_FOR=$(( NOW - LAST_CHANGE_AT ))
    if (( SILENT_FOR >= THRESHOLD && ! CAPTURED_THIS_STALL )); then
        echo "$(/bin/date '+%H:%M:%S') no trace activity for ${SILENT_FOR}s -- stall suspected"
        capture "$LAST_CHANGE_AT"
        CAPTURED_THIS_STALL=1
    fi
done

#!/usr/bin/env zsh
#
# Periodic BetterTouchTool restart and widget refresh.
#
# Root cause, confirmed by live capture rather than just code reading (see
# widgets/timer-widget.sh and actions/freeze-catch.sh): a `sample` taken while
# frozen (~/Library/Caches/btt-widgets/freeze-samples/20260808-180758/)
# caught BetterTouchTool's own main thread 100% busy inside AppKit --
# -[NSWindow recalculateKeyViewLoop] recursing dozens of frames deep through
# NSPerformVisuallyAtomicChange/_layoutSubtreeWithOldSize: while decoding a
# NIB. That blocks BTT's run loop, which is also what dispatches widget ticks
# to its BetterTouchToolShellScriptRunner XPC helper -- so nothing runs,
# system-wide, until that layout pass finishes. timer-widget.sh froze the same
# way with zero network or Apple Music work involved, which rules out any
# particular widget's own logic (including the historical lyrics nohup/setsid
# gap noted in actions/track-changed.sh -- a real
# bug, but not this one: it can only wedge the lyrics widget's own tick, not
# every other widget at once).
#
# The actual fix is on BTT's side (an AppKit performance bug in its own
# code); there is nothing in this repo to patch. Until upstream fixes it,
# this script is invoked every 5 seconds to probe timer-widget and then
# explicitly refreshes every script widget after a restart. A persisted
# 3-minute deadline keeps the preventive restart cadence independent of the
# liveness-probe cadence. A failed probe is evidence that BTT cannot dispatch
# the Touch Bar refresh and therefore bypasses idle deferral.
#
# The idle-deferral below (added to preserve keyboard focus during a restart)
# used to have no cap: as long as HIDIdleTime kept coming back under 60s at
# every 3-minute check, it deferred forever. trace.tsv confirmed this let real
# freezes run 5-10 minutes uninterrupted -- freeze-guard.log shows 43
# consecutive "deferred -- active user" lines in a row (2026-08-08 23:53 to
# 2026-08-09 01:06) with a 603s trace gap inside that exact window. Ordinary
# activity in some other app is not evidence BTT itself is responsive, so
# MAX_CONSECUTIVE_DEFERS below bounds the total postponement instead of
# leaving it open-ended.
#
# This file is the documented, version-controlled source. The LaunchAgent
# does not run it from here: launchd spawns its own zsh under a TCC identity
# that macOS blocks from ~/Documents (same restriction noted in
# widgets/lib/btt-widget.sh re: btt_refresh_detached), even though BTT
# itself can read this whole repo fine. The actually-running copy lives at
# ~/Library/Application Support/BTT/btt-freeze-guard.sh -- re-copy this file
# there after making changes.
#

set -u

REPO_DIR="${BTT_REPO_DIR:-$HOME/Documents/BTT}"
LOG_DIR="${BTT_LOG_DIR:-$REPO_DIR/logs}"
LOG="$LOG_DIR/freeze-guard.log"
DEFER_COUNT_FILE="$HOME/Library/Caches/btt-widgets/freeze-guard.defers"
NEXT_RESTART_FILE="$HOME/Library/Caches/btt-widgets/freeze-guard.next-restart"
TRACE="$HOME/Library/Caches/btt-widgets/trace.tsv"
TIMER_WIDGET_UUID="E25C395A-FE13-4216-BC59-6317FD0454BF"
PROBE_INTERVAL="${BTT_TIMER_PROBE_INTERVAL:-5}"
RESTART_STARTUP_GRACE="${BTT_RESTART_STARTUP_GRACE:-30}"
IDLE_MIN_SECONDS=60
RESTART_KEYBOARD_IDLE_SECONDS=1
PREVENTIVE_RESTART_INTERVAL="${BTT_PREVENTIVE_RESTART_INTERVAL:-180}"
# One skipped interval, not zero: still absorbs a brief burst of typing
# elsewhere without yanking focus. Not unbounded: caps the worst case at two
# 3-minute intervals instead of running until an idle gap happens to appear.
MAX_CONSECUTIVE_DEFERS=1

mkdir -p "$LOG_DIR" 2>/dev/null

log() {
	echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"
}

timer_trace_stamp() {
	/usr/bin/awk -F '\t' '$2 == "timer-widget" && $3 == "widget" { latest = $1 } END { printf "%.6f\n", latest + 0 }' "$TRACE" 2>/dev/null || printf '0\n'
}

timer_trace_row() {
	/usr/bin/awk -F '\t' '$2 == "timer-widget" && $3 == "widget" { latest = $0 } END { print latest }' "$TRACE" 2>/dev/null
}

request_timer_refresh() {
	/usr/bin/osascript -e "tell application \"BetterTouchTool\" to refresh_widget \"$TIMER_WIDGET_UUID\"" >/dev/null 2>&1 &
	TIMER_REFRESH_PID=$!
}

while true; do
	defers=0
	if [[ -f "$DEFER_COUNT_FILE" ]]; then
		defers="$(<"$DEFER_COUNT_FILE")"
		[[ "$defers" =~ ^[0-9]+$ ]] || defers=0
	fi

	before_stamp="$(timer_trace_stamp)"
	before_trace="$(timer_trace_row)"
	request_timer_refresh
	sleep "$PROBE_INTERVAL"
	after_stamp="$(timer_trace_stamp)"
	after_trace="$(timer_trace_row)"
	kill "$TIMER_REFRESH_PID" >/dev/null 2>&1 || true
	timer_responsive=1
	if (( after_stamp > before_stamp )); then
		log "timer-widget responsive trace=${after_trace:-none}"
	else
		timer_responsive=0
		log "timer-widget unresponsive for ${PROBE_INTERVAL}s -- final_trace=${before_trace:-none}"
	fi

	now="$(date +%s)"
	next_restart=0
	if [[ -f "$NEXT_RESTART_FILE" ]]; then
		next_restart="$(<"$NEXT_RESTART_FILE")"
		[[ "$next_restart" =~ ^[0-9]+$ ]] || next_restart=0
	fi
	if (( next_restart == 0 )); then
		next_restart=$(( now + PREVENTIVE_RESTART_INTERVAL ))
		echo "$next_restart" > "$NEXT_RESTART_FILE"
	fi

	if (( timer_responsive && now < next_restart )); then
		continue
	fi

	idle_nanoseconds="$(/usr/sbin/ioreg -c IOHIDSystem -d 4 -w 0 2>/dev/null | /usr/bin/awk -F'= ' '/"HIDIdleTime"/ { print $2; exit }')"
	if (( timer_responsive )) && [[ "$idle_nanoseconds" =~ ^[0-9]+$ ]] && (( idle_nanoseconds < IDLE_MIN_SECONDS * 1000000000 )) \
		&& (( defers < MAX_CONSECUTIVE_DEFERS )); then
		echo $(( defers + 1 )) > "$DEFER_COUNT_FILE"
		echo $(( now + PREVENTIVE_RESTART_INTERVAL )) > "$NEXT_RESTART_FILE"
		log "scheduled restart deferred -- active user ($(( defers + 1 ))/$MAX_CONSECUTIVE_DEFERS)"
		continue
	fi

	rm -f "$DEFER_COUNT_FILE" 2>/dev/null
	echo $(( now + PREVENTIVE_RESTART_INTERVAL )) > "$NEXT_RESTART_FILE"
	if (( timer_responsive )); then
		log "scheduled restart -- final_timer_trace=$(timer_trace_row)"
	else
		log "unresponsive restart -- final_timer_trace=${before_trace:-none}"
	fi

	# Never terminate BTT while a key is still down: its event tap can otherwise
	# leave the front app believing a modifier remains pressed after relaunch.
	restart_idle_nanoseconds="$(/usr/sbin/ioreg -c IOHIDSystem -d 4 -w 0 2>/dev/null | /usr/bin/awk -F'= ' '/"HIDIdleTime"/ { print $2; exit }')"
	if [[ "$restart_idle_nanoseconds" =~ ^[0-9]+$ ]] && (( restart_idle_nanoseconds < RESTART_KEYBOARD_IDLE_SECONDS * 1000000000 )); then
		log "restart deferred -- keyboard activity within ${RESTART_KEYBOARD_IDLE_SECONDS}s"
		continue
	fi

	/usr/bin/osascript -e 'tell application "BetterTouchTool" to quit' >/dev/null 2>&1
	sleep 3
	# Belt and suspenders: the quit above can itself be a no-op if BTT is deep
	# enough into the wedge, so make sure it's actually gone before reopening.
	killall -9 BetterTouchTool BTTRelaunch >/dev/null 2>&1
	sleep 1
	# Keep the current app in front, but do not launch BTT hidden: `-j` also
	# hides its Touch Bar UI until the user manually reveals it.
	/usr/bin/open -g -a "BetterTouchTool"

refresh_widgets() {
	/usr/bin/osascript <<'APPLESCRIPT' >/dev/null 2>&1
tell application "BetterTouchTool"
	refresh_widget "59F8C568-022F-4BD9-B3EB-63A7676592DF"
	refresh_widget "CF76E4C0-5986-41F9-8F3E-00A6C8F160FE"
	refresh_widget "E19BB023-5060-4A56-95C8-6E7402779870"
	refresh_widget "8C95B746-77DA-4B76-A966-6EBB10E755F4"
	refresh_widget "508ECCA7-BAD5-469D-9418-94E2C370AE37"
	refresh_widget "E25C395A-FE13-4216-BC59-6317FD0454BF"
end tell
APPLESCRIPT
}

# BTT can accept an Apple Event before its Touch Bar widget runner is ready.
# Retry the inexpensive redraw request while its startup finishes; each widget
# serializes real work with its refresh lock.
	for delay in 5 5 5; do
		sleep "$delay"
		refresh_widgets
	done
	log "restart refresh sequence complete -- waiting ${RESTART_STARTUP_GRACE}s before resuming probes"
	sleep "$RESTART_STARTUP_GRACE"
done

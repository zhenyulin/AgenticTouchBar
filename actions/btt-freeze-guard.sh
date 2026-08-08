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
# this script restarts BTT on every 3-minute LaunchAgent interval and then
# explicitly refreshes every script widget. Before deciding whether to defer
# for user activity, it explicitly refreshes timer-widget and waits for a new
# timer trace row. A failed probe is evidence that BTT cannot dispatch the
# Touch Bar refresh and therefore bypasses idle deferral.
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

LOG="$HOME/Library/Caches/btt-widgets/freeze-guard.log"
DEFER_COUNT_FILE="$HOME/Library/Caches/btt-widgets/freeze-guard.defers"
TRACE="$HOME/Library/Caches/btt-widgets/trace.tsv"
TIMER_WIDGET_UUID="E25C395A-FE13-4216-BC59-6317FD0454BF"
TIMER_REFRESH_TIMEOUT="${BTT_TIMER_REFRESH_TIMEOUT:-15}"
TIMER_REFRESH_POLL="${BTT_TIMER_REFRESH_POLL:-1}"
IDLE_MIN_SECONDS=60
# One skipped interval, not zero: still absorbs a brief burst of typing
# elsewhere without yanking focus. Not unbounded: caps the worst case at two
# 3-minute intervals instead of running until an idle gap happens to appear.
MAX_CONSECUTIVE_DEFERS=1

mkdir -p "$(dirname "$LOG")" 2>/dev/null

timer_trace_stamp() {
	/usr/bin/awk -F '\t' '$2 == "timer-widget" && $3 == "widget" { latest = $1 } END { printf "%.6f\n", latest + 0 }' "$TRACE" 2>/dev/null || printf '0\n'
}

timer_widget_responsive() {
	local before after probe_pid elapsed
	before="$(timer_trace_stamp)"
	/usr/bin/osascript -e "tell application \"BetterTouchTool\" to refresh_widget \"$TIMER_WIDGET_UUID\"" >/dev/null 2>&1 &
	probe_pid=$!

	for (( elapsed = 0; elapsed < TIMER_REFRESH_TIMEOUT; elapsed++ )); do
		after="$(timer_trace_stamp)"
		if (( after > before )); then
			kill "$probe_pid" >/dev/null 2>&1 || true
			return 0
		fi
		sleep "$TIMER_REFRESH_POLL"
	done

	kill "$probe_pid" >/dev/null 2>&1 || true
	return 1
}

defers=0
if [[ -f "$DEFER_COUNT_FILE" ]]; then
	defers="$(<"$DEFER_COUNT_FILE")"
	[[ "$defers" =~ ^[0-9]+$ ]] || defers=0
fi

timer_responsive=1
if ! timer_widget_responsive; then
	timer_responsive=0
	echo "$(date '+%Y-%m-%d %H:%M:%S') timer-widget unresponsive for ${TIMER_REFRESH_TIMEOUT}s -- restarting BTT" >> "$LOG"
fi

idle_nanoseconds="$(/usr/sbin/ioreg -c IOHIDSystem -d 4 -w 0 2>/dev/null | /usr/bin/awk -F'= ' '/"HIDIdleTime"/ { print $2; exit }')"
if (( timer_responsive )) && [[ "$idle_nanoseconds" =~ ^[0-9]+$ ]] && (( idle_nanoseconds < IDLE_MIN_SECONDS * 1000000000 )) \
	&& (( defers < MAX_CONSECUTIVE_DEFERS )); then
	echo $(( defers + 1 )) > "$DEFER_COUNT_FILE"
	echo "$(date '+%Y-%m-%d %H:%M:%S') scheduled restart deferred -- active user ($(( defers + 1 ))/$MAX_CONSECUTIVE_DEFERS)" >> "$LOG"
	exit 0
fi

rm -f "$DEFER_COUNT_FILE" 2>/dev/null
echo "$(date '+%Y-%m-%d %H:%M:%S') scheduled restart -- restarting BTT" >> "$LOG"

/usr/bin/osascript -e 'tell application "BetterTouchTool" to quit' >/dev/null 2>&1
sleep 3
# Belt and suspenders: the quit above can itself be a no-op if BTT is deep
# enough into the wedge, so make sure it's actually gone before reopening.
killall -9 BetterTouchTool BTTRelaunch >/dev/null 2>&1
sleep 1
# Preserve the front app and current keyboard focus during the scheduled restart.
/usr/bin/open -gj -a "BetterTouchTool"

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

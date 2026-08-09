#!/usr/bin/env zsh
#
# Periodic BetterTouchTool restart and widget refresh.
#
# Root cause, confirmed by live capture rather than just code reading (see
# widgets/timer-widget.sh and actions/freeze-catch.sh): a `sample` taken while
# frozen (logs/freeze-samples/20260808-180758/)
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
# this script actively refreshes the Latency widget and watches its dedicated
# history timestamp. BTT is restarted only when the refresh does not dispatch
# the widget within the configured timeout.
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
CACHE_DIR="${BTT_WIDGET_CACHE_DIR:-$REPO_DIR/cache}"
LOG_DIR="${BTT_LOG_DIR:-$REPO_DIR/logs}"
LOG="$LOG_DIR/freeze-guard.log"
LATENCY_HISTORY_FILE="${BTT_LATENCY_HISTORY_FILE:-$LOG_DIR/latency-history.tsv}"
LYRICS_TRACE_FILE="${BTT_LYRICS_TRACE_FILE:-$LOG_DIR/lyrics/trace.tsv}"
LATENCY_WIDGET_UUID="59F8C568-022F-4BD9-B3EB-63A7676592DF"
PROBE_INTERVAL="${BTT_LATENCY_PROBE_INTERVAL:-10}"
[[ "$PROBE_INTERVAL" =~ ^[0-9]+([.][0-9]+)?$ ]] || PROBE_INTERVAL=10
(( PROBE_INTERVAL > 0 )) || PROBE_INTERVAL=10
DEFAULT_LATENCY_TIMEOUT=22
if [[ -n "${BTT_LATENCY_TIMEOUT:-}" ]]; then
	LATENCY_TIMEOUT="$BTT_LATENCY_TIMEOUT"
else
	LATENCY_TIMEOUT="$DEFAULT_LATENCY_TIMEOUT"
fi
[[ "$LATENCY_TIMEOUT" =~ ^[0-9]+([.][0-9]+)?$ ]] || LATENCY_TIMEOUT="$DEFAULT_LATENCY_TIMEOUT"
(( LATENCY_TIMEOUT > 0 )) || LATENCY_TIMEOUT="$DEFAULT_LATENCY_TIMEOUT"
LYRICS_TRACE_MAX_AGE="${BTT_LYRICS_TRACE_MAX_AGE:-5}"
[[ "$LYRICS_TRACE_MAX_AGE" =~ ^[0-9]+([.][0-9]+)?$ ]] || LYRICS_TRACE_MAX_AGE=5
(( LYRICS_TRACE_MAX_AGE > 0 )) || LYRICS_TRACE_MAX_AGE=5
RESTART_STARTUP_GRACE="${BTT_RESTART_STARTUP_GRACE:-$LATENCY_TIMEOUT}"
RESTART_KEYBOARD_IDLE_SECONDS=1

mkdir -p "$CACHE_DIR" "$LOG_DIR" 2>/dev/null

log() {
	echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"
}

latency_history_stamp() {
	/usr/bin/awk -F '\t' '($1 + 0) > latest { latest = $1 + 0 } END { printf "%.6f\n", latest + 0 }' "$LATENCY_HISTORY_FILE" 2>/dev/null || printf '0\n'
}

lyrics_trace_lag() {
	local now_stamp
	now_stamp="$(/bin/date +%s)"
	/usr/bin/awk -F '\t' -v now="$now_stamp" '
		$2 == "lyrics" && $3 == "sample" && ($1 + 0) > latest_sample {
			latest_sample = $1 + 0
			sample_state = $5
		}
		$2 == "lyrics" && $3 == "widget" && ($1 + 0) > latest_widget {
			latest_widget = $1 + 0
		}
		END {
			if (sample_state != "playing" || latest_widget == 0) {
				print ""
			} else {
				printf "%.6f\n", now - latest_widget
			}
		}
	' "$LYRICS_TRACE_FILE" 2>/dev/null || true
}

restart_duration() {
	local file="$1"
	/usr/bin/awk -F '\t' '
		$1 == "+" {
			first = ""
			last = ""
			next
		}
		$1 ~ /^[0-9]+([.][0-9]+)?$/ {
			stamp = $1 + 0
			if (first == "" || stamp < first) first = stamp
			if (last == "" || stamp > last) last = stamp
		}
		END {
			if (first == "" || last == "") print "0"
			else printf "%.3f\n", last - first
		}
	' "$file" 2>/dev/null || printf '0\n'
}

write_restart_marker() {
	local latency_duration lyrics_duration
	latency_duration="$(restart_duration "$LATENCY_HISTORY_FILE")"
	lyrics_duration="$(restart_duration "$LYRICS_TRACE_FILE")"
	mkdir -p "${LATENCY_HISTORY_FILE:h}" "${LYRICS_TRACE_FILE:h}" 2>/dev/null || true
	printf '+\t%s\tBTT restart\n' "$latency_duration" >> "$LATENCY_HISTORY_FILE" 2>/dev/null || true
	printf '+\t%s\tBTT restart\n' "$lyrics_duration" >> "$LYRICS_TRACE_FILE" 2>/dev/null || true
}

request_latency_refresh() {
	: > "$CACHE_DIR/$LATENCY_WIDGET_UUID.force" 2>/dev/null || true
	/usr/bin/osascript -e "tell application \"BetterTouchTool\" to refresh_widget \"$LATENCY_WIDGET_UUID\"" >/dev/null 2>&1 &
	LATENCY_REFRESH_PID=$!
}

while true; do
	before_latency_stamp="$(latency_history_stamp)"
	request_latency_refresh
	sleep "$LATENCY_TIMEOUT"
	kill "$LATENCY_REFRESH_PID" >/dev/null 2>&1 || true
	after_latency_stamp="$(latency_history_stamp)"
	if (( after_latency_stamp > before_latency_stamp )); then
		lyrics_lag="$(lyrics_trace_lag)"
		if [[ -z "$lyrics_lag" ]]; then
			log "clash-latency refresh responsive -- before=${before_latency_stamp} after=${after_latency_stamp} lyrics=not-playing"
			continue
		fi
		if /usr/bin/awk -v lag="$lyrics_lag" -v max_age="$LYRICS_TRACE_MAX_AGE" 'BEGIN { exit !(lag <= max_age) }'; then
			log "clash-latency refresh responsive -- before=${before_latency_stamp} after=${after_latency_stamp} lyrics_lag=${lyrics_lag}s"
			continue
		fi
		log "lyrics trace stale while playing for ${lyrics_lag}s -- max=${LYRICS_TRACE_MAX_AGE}s"
	fi

	if (( after_latency_stamp <= before_latency_stamp )); then
		log "clash-latency refresh unresponsive for ${LATENCY_TIMEOUT}s -- before=${before_latency_stamp} after=${after_latency_stamp}"
	fi

	# Never terminate BTT while a key is still down: its event tap can otherwise
	# leave the front app believing a modifier remains pressed after relaunch.
	restart_idle_nanoseconds="$(/usr/sbin/ioreg -c IOHIDSystem -d 4 -w 0 2>/dev/null | /usr/bin/awk -F'= ' '/"HIDIdleTime"/ { print $2; exit }')"
	if [[ "$restart_idle_nanoseconds" =~ ^[0-9]+$ ]] && (( restart_idle_nanoseconds < RESTART_KEYBOARD_IDLE_SECONDS * 1000000000 )); then
		log "restart deferred -- keyboard activity within ${RESTART_KEYBOARD_IDLE_SECONDS}s"
		continue
	fi

	write_restart_marker
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

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
# this script watches two independent signals and restarts BTT when either
# says the Touch Bar has stopped moving:
#
#   1. The widget heartbeat: the newest tick of any widget in
#      HEARTBEAT_WIDGETS, from logs/trace.tsv. Those widgets run on a fixed
#      BTT interval and write their trace row before doing any real work, so
#      a gap there means BTT is not dispatching widget scripts at all --
#      which is exactly the wedge above. This replaced an earlier probe that
#      forced a refresh of the Clash latency widget and watched its history
#      file: that file only gets a row after a network round trip to the
#      Clash controller, so every slow or skipped probe read as a freeze. It
#      restarted BTT roughly every two minutes while BTT was demonstrably
#      fine, and only the keyboard-idle deferral below kept it from being
#      worse.
#
#   2. The lyrics render deadline. A frozen widget and a working one look
#      identical in the heartbeat if the widget is running but reprinting its
#      previous frame -- which is what the lyrics widget does when its Apple
#      Music sampler dies or its lock is held (see render.py's
#      emit_last_output paths). So every tick that renders a lyric of its own
#      records, in logs/lyrics/render.json, the wall clock time at which the
#      line on screen is due to be replaced by the next LRC line. A deadline
#      in the past means the display has stopped moving, and needs no LRC
#      knowledge here to check. It is also self-scaling: a long instrumental
#      break sets a deadline far out instead of tripping a fixed threshold.
#
# Every restart is also recorded in logs/restart.tsv: a `before` row the
# instant a restart is triggered (with the reason), and a `first-tick` row
# when the next timer/clash-latency tick lands after it -- the first proof
# the Touch Bar is rendering again. Both rows share the trigger timestamp
# as restart_id, so the ledger can be joined with logs/freeze-guard.log and
# the `+` markers in the traces.
#
# Not every stuck display is BTT's fault, and restarting BTT does not fix the
# ones that are not, so an overdue deadline is read together with the widget's
# newest trace row before reaching for the kill. A "no_sample" tick means the
# Apple Music sampler has nothing fresh to draw from and a "locked" tick means
# the previous tick is still running; both are logged and left alone.
#
# This file is the documented, version-controlled source. The LaunchAgent
# does not run it from here: launchd spawns its own zsh under a TCC identity
# that macOS blocks from ~/Documents (same restriction noted in
# widgets/lib/btt-widget.sh re: btt_refresh_detached), even though BTT
# itself can read this whole repo fine. The actually-running copy lives at
# ~/Library/Application Support/BTT/btt-freeze-guard.sh -- re-copy this file
# there after making changes, and `launchctl kickstart -k` the agent so the
# running process is the new one.
#

set -u

# Sub-second wall clock, via $EPOCHREALTIME. The signals watched here are
# timestamps written with fractional seconds, and `date +%s` truncates: it
# reads up to a second in the past, which shows up as negative ages.
zmodload zsh/datetime

REPO_DIR="${BTT_REPO_DIR:-$HOME/Documents/BTT}"
CACHE_DIR="${BTT_WIDGET_CACHE_DIR:-$REPO_DIR/cache}"
LOG_DIR="${BTT_LOG_DIR:-$REPO_DIR/logs}"
LOG="$LOG_DIR/freeze-guard.log"
# Structured restart ledger. Append-only TSV; one row per phase of a restart
# (see restart_log). Deliberately a separate file from freeze-guard.log so a
# restart record is one awk pass away instead of a grep through prose.
RESTART_LOG="${BTT_RESTART_LOG:-$LOG_DIR/restart.tsv}"

# The shell widgets' shared trace, written by lib/btt-widget.sh.
WIDGET_TRACE_FILE="${BTT_WIDGET_TRACE_FILE:-$LOG_DIR/trace.tsv}"
# Which widgets' ticks count as the heartbeat. Both run on a fixed interval
# from BTT and write their trace row before doing any real work, so a gap in
# them is BTT and nothing else: timer-widget every 10.0s with no network or
# Apple Music work at all, clash-latency every 13.1s with its network probe
# detached into a separate refresh process.
#
# The lyrics widget is deliberately not one of them, even though it ticks
# fastest. Its cadence is neither fixed nor unconditional -- it renders about
# every 2.4s while a track plays and stops entirely when Music is paused or
# the widget is hidden -- so including it made the heartbeat track the lyrics
# widget's own health instead of BTT's, and hid exactly the case the lyrics
# receipt exists to catch. The two signals are now independent.
HEARTBEAT_WIDGETS="${BTT_HEARTBEAT_WIDGETS:-timer-widget clash-latency}"
TIMER_WIDGET_UUID="E25C395A-FE13-4216-BC59-6317FD0454BF"

LYRICS_TRACE_FILE="${BTT_LYRICS_TRACE_FILE:-$LOG_DIR/lyrics/trace.tsv}"
# The render receipt is deliberately under logs/. launchd's zsh gets EPERM opening
# anything under cache/ -- macOS gates ~/Documents and only logs/ carries the
# com.apple.macl grant that lets this process through, which is also why the
# running copy of this script lives outside the repo.
LYRICS_RENDER_FILE="${BTT_LYRICS_RENDER_FILE:-$LOG_DIR/lyrics/render.json}"
LYRICS_VALUE_FILE="${BTT_LYRICS_VALUE_FILE:-$CACHE_DIR/lyrics.value}"
LYRICS_LAST_FILE="${BTT_LYRICS_LAST_FILE:-$CACHE_DIR/last.txt}"

number_or() {
	local value="$1" fallback="$2"
	[[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] || value="$fallback"
	(( value > 0 )) || value="$fallback"
	print -r -- "$value"
}

# How often the signals are read. Cheap: two awk passes over local files.
PROBE_INTERVAL="$(number_or "${BTT_PROBE_INTERVAL:-10}" 10)"
# How long the widget heartbeat may go quiet before BTT is considered wedged.
# timer-widget ticks every 10s, so this is four missed ticks.
HEARTBEAT_TIMEOUT="$(number_or "${BTT_HEARTBEAT_TIMEOUT:-45}" 45)"
# Slack on the lyrics deadline, covering a slow tick plus sampler jitter.
LYRICS_OVERDUE_GRACE="$(number_or "${BTT_LYRICS_OVERDUE_GRACE:-3}" 3)"
# Consecutive overdue probes before restarting. The heartbeat is proof BTT is
# alive in this case, so the display being stuck is the weaker of the two
# signals and gets asked twice more before it costs a restart.
LYRICS_OVERDUE_STRIKES="$(number_or "${BTT_LYRICS_OVERDUE_STRIKES:-3}" 3)"
# How long the widget may go without rendering a frame while the track is
# playing. It renders about once a second, so this is a very long silence --
# and unlike the deadline it also covers the outcomes that schedule no change
# at all, which would otherwise mask the widget dying underneath them.
LYRICS_RENDER_MAX_AGE="$(number_or "${BTT_LYRICS_RENDER_MAX_AGE:-30}" 30)"
RESTART_STARTUP_GRACE="$(number_or "${BTT_RESTART_STARTUP_GRACE:-15}" 15)"
RESTART_ACTION_TIMEOUT="$(number_or "${BTT_RESTART_ACTION_TIMEOUT:-4}" 4)"
RESTART_REFRESH_TIMEOUT="$(number_or "${BTT_RESTART_REFRESH_TIMEOUT:-2}" 2)"
RESTART_KEYBOARD_IDLE_SECONDS=1
# How long to wait for a held modifier or mouse button to be released before
# giving up on this probe's restart. Long enough for a click or a chord to
# finish, short enough that a real wedge is not prolonged.
RESTART_INPUT_WAIT="$(number_or "${BTT_RESTART_INPUT_WAIT:-5}" 5)"
# How long the checks below may keep postponing a restart before it goes ahead
# anyway. A deferral is a delay, not a veto: BTT stays wedged for minutes at a
# time (trace.tsv has a 603s gap), and someone typing through it would
# otherwise hold the restart off for exactly as long as they keep working --
# which is when they most want the Touch Bar back. Past this deadline the
# stuck-modifier risk is the lesser of the two, and it is also self-diagnosing:
# nobody holds Shift for a minute, so input still reported held that long is
# already latched, and a restart is as likely to clear it as to cause it.
RESTART_DEFER_MAX="$(number_or "${BTT_RESTART_DEFER_MAX:-60}" 60)"
GUARD_DIR="${0:A:h}"
HID_STATE_BIN="${BTT_HID_STATE_BIN:-$GUARD_DIR/hid-state}"

mkdir -p "$CACHE_DIR" "$LOG_DIR" 2>/dev/null

log() {
	echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"
}

# Only log a repeated verdict once, so a healthy day is a handful of lines
# rather than one every probe interval. Deduplicated on a caller-supplied key
# rather than on the message, because every message carries ages that differ
# on each probe -- with the heartbeat now sampling one fixed-interval widget
# it sweeps 0-10s -- and would defeat the check outright.
LAST_VERDICT=""
log_verdict() {
	local key="$1"
	shift
	[[ "$key" == "$LAST_VERDICT" ]] && return 0
	LAST_VERDICT="$key"
	log "$@"
}

# --- Reading the signals ----------------------------------------------------

# The receipt and sample files are single-line JSON written by
# lyrics/cache.py's atomic_write_json, so a field can be picked out without a
# JSON parser -- and without making this guard depend on python3 or jq being
# healthy, which is the thing it may well be watching fail.
json_number() {
	[[ -f "$1" ]] || return 0
	/usr/bin/awk -v key="\"$2\"" '
		{
			start = index($0, key)
			if (start == 0) next
			rest = substr($0, start + length(key))
			sub(/^[ \t]*:[ \t]*/, "", rest)
			if (match(rest, /^-?[0-9]+([.][0-9]+)?([eE][-+]?[0-9]+)?/)) {
				print substr(rest, RSTART, RLENGTH)
			}
			exit
		}
	' "$1" 2>/dev/null
}

json_string() {
	[[ -f "$1" ]] || return 0
	/usr/bin/awk -v key="\"$2\"" '
		{
			start = index($0, key)
			if (start == 0) next
			rest = substr($0, start + length(key))
			if (sub(/^[ \t]*:[ \t]*"/, "", rest) && match(rest, /^[^"]*/)) {
				print substr(rest, RSTART, RLENGTH)
			}
			exit
		}
	' "$1" 2>/dev/null
}

# Seconds since the last ordinary widget tick, from whichever of the two
# traces is newer. Rows whose first field is not a timestamp are the restart
# markers written below.
heartbeat_age() {
	/usr/bin/awk -F '\t' -v now="$EPOCHREALTIME" -v widgets="$HEARTBEAT_WIDGETS" '
		BEGIN {
			count = split(widgets, names, " ")
			for (i = 1; i <= count; i++) watched[names[i]] = 1
		}
		$3 == "widget" && $1 ~ /^[0-9]+([.][0-9]+)?$/ && ($2 in watched) {
			if (($1 + 0) > latest) latest = $1 + 0
		}
		END {
			if (latest == 0) print "unknown"
			else printf "%.1f\n", (now - latest > 0 ? now - latest : 0)
		}
	' "$WIDGET_TRACE_FILE" 2>/dev/null || print -r -- unknown
}

# The newest lyrics widget tick, as "<outcome> <sample_age_ms>". Its trace
# row is the only other thing worth knowing when the display is overdue: it
# says whether the widget was even in a position to render a new frame.
lyrics_last_tick() {
	/usr/bin/awk -F '\t' '
		$2 == "lyrics" && $3 == "widget" && $1 ~ /^[0-9]+([.][0-9]+)?$/ && ($1 + 0) > latest {
			latest = $1 + 0
			outcome = $5
			age = "none"
			if (match($6, /sample_age_ms=[^ ]+/)) {
				age = substr($6, RSTART + 14, RLENGTH - 14)
			}
		}
		END { if (latest) print outcome, age }
	' "$LYRICS_TRACE_FILE" 2>/dev/null
}

lyrics_value_matches_last_output() {
	[[ -f "$LYRICS_VALUE_FILE" && -f "$LYRICS_LAST_FILE" ]] || return 2
	cmp -s "$LYRICS_VALUE_FILE" "$LYRICS_LAST_FILE"
}

lyrics_cache_state() {
	lyrics_value_matches_last_output
	case $? in
		0) print -r -- "match" ;;
		1) print -r -- "mismatch" ;;
		*) print -r -- "unavailable" ;;
	esac
}

# Why the display is not moving, given that it should be: the widget's newest
# trace row names the two paths that reprint the previous frame on purpose,
# and neither is BTT's fault or fixable by restarting it.
lyrics_blame() {
	local tick outcome sample_age
	tick=(${=$(lyrics_last_tick)})
	outcome="${tick[1]:-none}"
	sample_age="${tick[2]:-none}"
	case "$outcome" in
		no_sample) print -r -- "sampler-stale sample_age_ms=$sample_age" ;;
		locked) print -r -- "widget-locked" ;;
		*) print -r -- "$1 tick=$outcome" ;;
	esac
}

# One of: idle / no-receipt / ok / sampler-stale / widget-locked / stalled /
# overdue, plus detail.
lyrics_display_state() {
	local now render_at render_state render_deadline render_outcome overdue

	now="$EPOCHREALTIME"
	render_at="$(json_number "$LYRICS_RENDER_FILE" at)"
	if [[ -z "$render_at" ]]; then
		print -r -- "no-receipt"
		return
	fi
	render_outcome="$(json_string "$LYRICS_RENDER_FILE" outcome)"

	# The receipt's own view of the player. Reading cache/lyrics/state.json
	# would be the more direct source, but launchd cannot open anything under
	# cache/ -- see RENDER_PATH in lyrics/config.py.
	render_state="$(json_string "$LYRICS_RENDER_FILE" state)"
	if [[ "$render_state" != "playing" ]]; then
		printf 'idle state=%s\n' "${render_state:-unknown}"
		return
	fi

	# Playing, and this widget renders about once a second, so a receipt this
	# old means it has stopped rendering entirely -- while the heartbeat says
	# the other widgets are still being dispatched. That is the one shape a
	# deadline cannot catch, because the outcomes with no deadline of their
	# own (an instrumental track, a fetch still running) would otherwise read
	# as healthy forever once the widget died underneath them.
	if (( now - render_at > LYRICS_RENDER_MAX_AGE )); then
		lyrics_blame "$(printf 'stalled render_age=%.1fs outcome=%s' \
			"$(( now - render_at ))" "${render_outcome:-?}")"
		return
	fi

	# Absent when nothing is due to change: instrumental, no lyrics found, or
	# a fetch still running for a new track.
	render_deadline="$(json_number "$LYRICS_RENDER_FILE" next_change_at)"
	if [[ -z "$render_deadline" ]]; then
		printf 'ok no-scheduled-change outcome=%s render_age=%.1fs\n' \
			"${render_outcome:-?}" "$(( now - render_at ))"
		return
	fi

	overdue=$(( now - render_deadline - LYRICS_OVERDUE_GRACE ))
	if (( overdue <= 0 )); then
		printf 'ok due_in=%.1fs\n' "$(( render_deadline - now ))"
		return
	fi

	# The line on screen should have been replaced by now.
	lyrics_blame "$(printf 'overdue by=%.1fs render_age=%.1fs' \
		"$overdue" "$(( now - render_at ))")"
}

# --- Restarting -------------------------------------------------------------

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

# Append one row to logs/restart.tsv, creating the header on first use.
# Columns: phase, ts, restart_id, btt_pid, key, detail. restart_id is the
# trigger timestamp, shared by the `before` and `first-tick` rows of one
# restart so they can be joined.
restart_log() {
	local phase="$1" ts="$2" restart_id="$3" pid="$4" key="$5" detail="$6"
	[[ -f "$RESTART_LOG" ]] || \
		printf 'phase\tts\trestart_id\tbtt_pid\tkey\tdetail\n' > "$RESTART_LOG" 2>/dev/null || true
	printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
		"$phase" "$ts" "$restart_id" "$pid" "$key" "$detail" \
		>> "$RESTART_LOG" 2>/dev/null || true
}

# The first heartbeat widget tick strictly after `since`, as "<widget> <ts>",
# or empty. Used to close the restart ledger: the first timer/clash-latency
# render after a restart is the earliest proof the Touch Bar is moving again.
first_heartbeat_after() {
	local since="$1"
	/usr/bin/awk -F '\t' -v since="$since" -v widgets="$HEARTBEAT_WIDGETS" '
		BEGIN {
			count = split(widgets, names, " ")
			for (i = 1; i <= count; i++) watched[names[i]] = 1
		}
		$3 == "widget" && $1 ~ /^[0-9]+([.][0-9]+)?$/ && ($2 in watched) && ($1 + 0) > since {
			printf "%s %s", $2, $1
			exit
		}
	' "$WIDGET_TRACE_FILE" 2>/dev/null
}

write_restart_marker() {
	local widget_duration lyrics_duration
	widget_duration="$(restart_duration "$WIDGET_TRACE_FILE")"
	lyrics_duration="$(restart_duration "$LYRICS_TRACE_FILE")"
	mkdir -p "${WIDGET_TRACE_FILE:h}" "${LYRICS_TRACE_FILE:h}" 2>/dev/null || true
	printf '+\t%s\tBTT restart\n' "$widget_duration" >> "$WIDGET_TRACE_FILE" 2>/dev/null || true
	printf '+\t%s\tBTT restart\n' "$lyrics_duration" >> "$LYRICS_TRACE_FILE" 2>/dev/null || true
}

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

refresh_widgets_bounded() {
	local refresh_pid waited
	refresh_widgets &
	refresh_pid=$!
	for (( waited = 0; waited < RESTART_REFRESH_TIMEOUT * 4; waited++ )); do
		if ! kill -0 "$refresh_pid" 2>/dev/null; then
			wait "$refresh_pid" 2>/dev/null || true
			return 0
		fi
		sleep 0.25
	done
	kill "$refresh_pid" 2>/dev/null || true
	wait "$refresh_pid" 2>/dev/null || true
	log "restart widget refresh timed out after ${RESTART_REFRESH_TIMEOUT}s"
}

# A quiet heartbeat is worth one nudge before it is worth a restart: BTT can
# simply have nothing scheduled. This asks for the timer widget specifically,
# which does no network or Apple Music work, so a tick that follows is proof
# of dispatch rather than of anything else.
nudge_heartbeat() {
	/usr/bin/osascript -e \
		"tell application \"BetterTouchTool\" to refresh_widget \"$TIMER_WIDGET_UUID\"" \
		>/dev/null 2>&1 &
}

# Whether a modifier or mouse button is being held right now, asked of the
# window server rather than inferred from event timing. Prints the detail on
# stdout. See actions/hid-state.c for why HIDIdleTime cannot answer this.
input_held() {
	local state
	[[ -x "$HID_STATE_BIN" ]] || return 1
	state="$("$HID_STATE_BIN" 2>/dev/null)"
	[[ "$state" == "held "* ]] || return 1
	print -r -- "$state"
}

btt_pid() {
	/usr/bin/pgrep -x BetterTouchTool 2>/dev/null | /usr/bin/awk 'NR == 1 { print; exit }'
}

btt_running() {
	[[ -n "$(btt_pid)" ]]
}

# When the current run of deferrals began, or 0 when not deferring. Cleared by
# a restart and by the main loop the moment BTT looks healthy again, so an old
# run never makes a later restart skip its checks.
DEFER_SINCE=0
# Epoch (sub-second) at which the current restart was triggered, or 0 when no
# restart is awaiting its first heartbeat tick. Set by restart_btt and cleared
# by the main loop the moment that tick lands.
RESTART_TRIGGER_TS=0

# Record a deferral and report whether the deadline has run out. Callers past
# the deadline proceed anyway.
defer_restart() {
	local reason="$1"
	(( DEFER_SINCE )) || DEFER_SINCE=$EPOCHSECONDS
	log "restart deferred -- ${reason}"
	return 1
}

restart_input_reason() {
	local held waited idle_nanoseconds
	for (( waited = 0; waited < RESTART_INPUT_WAIT * 4; waited++ )); do
		held="$(input_held)" || break
		sleep 0.25
	done
	if held="$(input_held)"; then
		print -r -- "input still held after ${RESTART_INPUT_WAIT}s (${held})"
		return 1
	fi

	idle_nanoseconds="$(/usr/sbin/ioreg -c IOHIDSystem -d 4 -w 0 2>/dev/null | /usr/bin/awk -F'= ' '/"HIDIdleTime"/ { print $2; exit }')"
	if [[ "$idle_nanoseconds" =~ ^[0-9]+$ ]] && (( idle_nanoseconds < RESTART_KEYBOARD_IDLE_SECONDS * 1000000000 )); then
		print -r -- "keyboard activity within ${RESTART_KEYBOARD_IDLE_SECONDS}s"
		return 1
	fi
}

request_btt_restart() {
	local pid="$1" request_pid waited
	/usr/bin/osascript -e 'tell application "BetterTouchTool" to trigger_action "{\"BTTPredefinedActionType\":55}"' \
		>/dev/null 2>&1 &
	request_pid=$!
	for (( waited = 0; waited < RESTART_ACTION_TIMEOUT * 4; waited++ )); do
		if ! kill -0 "$pid" 2>/dev/null; then
			kill "$request_pid" 2>/dev/null || true
			wait "$request_pid" 2>/dev/null || true
			return 0
		fi
		sleep 0.25
	done
	kill "$request_pid" 2>/dev/null || true
	wait "$request_pid" 2>/dev/null || true
	return 1
}

terminate_btt() {
	local pid="$1" settle
	[[ "$(btt_pid)" == "$pid" ]] || return 0
	kill "$pid" 2>/dev/null || true
	for (( settle = 0; settle < RESTART_ACTION_TIMEOUT * 4; settle++ )); do
		[[ "$(btt_pid)" == "$pid" ]] || return 0
		sleep 0.25
	done
	log "BTT ignored SIGTERM for ${RESTART_ACTION_TIMEOUT}s -- escalating to SIGKILL"
	[[ "$(btt_pid)" == "$pid" ]] && kill -KILL "$pid" 2>/dev/null || true
	for (( settle = 0; settle < 8; settle++ )); do
		[[ "$(btt_pid)" == "$pid" ]] || return 0
		sleep 0.25
	done
	return 1
}

restart_btt() {
	# reason and detail name the signal that asked for this restart; both go
	# into the structured ledger (logs/restart.tsv).
	local reason="${1:-unknown}" detail="${2:-}"
	# A stopped process is an intentional quit, not a freeze. Do not reopen BTT
	# unless it is still running and has merely stopped dispatching widgets.
	local btt_before
	btt_before="$(btt_pid)"
	if [[ -z "$btt_before" ]]; then
		log "BTT is not running -- respecting quit"
		return 1
	fi

	# Overdue deferrals stop being honoured, so a wedge cannot outlast the fix.
	local overdue=0
	(( DEFER_SINCE && EPOCHSECONDS - DEFER_SINCE >= RESTART_DEFER_MAX )) && overdue=1

	# Never terminate BTT mid-gesture. Its event tap sits between the hardware
	# and every other app, and its trackpad actions post synthetic events of
	# their own, so tearing it down while a modifier or button is down can leave
	# the front app with a latched Shift or a phantom mouse-down that nothing
	# clears -- a stuck drag overlay in the editor being the usual tell.
	local input_reason deferral_age=0
	(( DEFER_SINCE )) && deferral_age=$(( EPOCHSECONDS - DEFER_SINCE ))
	if ! input_reason="$(restart_input_reason)"; then
		if (( ! overdue )); then
			defer_restart "$input_reason"
			return 1
		fi
		log "restarting despite ${input_reason} -- deferred $(( EPOCHSECONDS - DEFER_SINCE ))s, past ${RESTART_DEFER_MAX}s deadline"
	fi

	# The restart is about to actually happen. Record it before triggering so
	# the ledger survives even if the restart itself wedges everything.
	local trigger_ts="$EPOCHREALTIME"
	RESTART_TRIGGER_TS=$trigger_ts
	restart_log before "$trigger_ts" "$trigger_ts" "$btt_before" "$reason" "$detail"
	log "restarting BTT -- reason=${reason} ${detail}"

	DEFER_SINCE=0
	write_restart_marker
	# Prefer BTT's own documented restart action. It lets BTT tear down its
	# event tap and coordinate with BTTRelaunch, avoiding a blind kill while
	# still keeping the frontmost app in place.
	local restart_mode="BTT restart action"
	if ! request_btt_restart "$btt_before"; then
		# A wedged main thread may never process the Apple Event. Re-check input
		# immediately before the destructive fallback because the request above
		# may have taken several seconds to time out.
		if ! input_reason="$(restart_input_reason)"; then
			if (( ! overdue )); then
				defer_restart "$input_reason"
				return 1
			fi
			log "fallback restart despite ${input_reason} -- deferred ${deferral_age}s, past ${RESTART_DEFER_MAX}s deadline"
		fi
		restart_mode="targeted signal fallback"
		terminate_btt "$btt_before" || {
			log "BTT PID ${btt_before} did not exit after SIGKILL"
			return 1
		}
	fi

	# Leave BTTRelaunch alive. If it notices the main process first, use its
	# replacement; only call open when neither restart path produced one.
	local launch_wait
	for (( launch_wait = 0; launch_wait < RESTART_ACTION_TIMEOUT * 4; launch_wait++ )); do
		btt_running && break
		sleep 0.25
	done
	if ! btt_running; then
		# Keep the current app in front, but do not launch BTT hidden: `-j` also
		# hides its Touch Bar UI until the user manually reveals it.
		/usr/bin/open -g -a "BetterTouchTool"
	fi

	# BTT can accept an Apple Event before its Touch Bar widget runner is
	# ready. Retry the inexpensive redraw request at short, bounded intervals;
	# each widget serializes real work with its refresh lock.
	local delay
	for delay in 1 2 4; do
		sleep "$delay"
		refresh_widgets_bounded
	done
	log "${restart_mode} refresh sequence complete -- waiting ${RESTART_STARTUP_GRACE}s before resuming probes"
	sleep "$RESTART_STARTUP_GRACE"
	return 0
}

# --- Main loop --------------------------------------------------------------

overdue_strikes=0
nudged=0

while true; do
	sleep "$PROBE_INTERVAL"

	beat="$(heartbeat_age)"
	if [[ "$beat" == "unknown" ]]; then
		log_verdict no-trace "no ${HEARTBEAT_WIDGETS// / or } tick on record -- nothing to watch"
		continue
	fi

	if (( beat > HEARTBEAT_TIMEOUT )); then
		overdue_strikes=0
		log "no widget tick for ${beat}s -- max=${HEARTBEAT_TIMEOUT}s"
		LAST_VERDICT=""
		restart_btt heartbeat-timeout \
			"no widget tick for ${beat}s -- max=${HEARTBEAT_TIMEOUT}s" && nudged=0
		continue
	fi

	# Close an open restart in the ledger: the first heartbeat tick after the
	# trigger proves the Touch Bar is rendering again. Checked before the
	# nudge branch so the row is written even when the beat is still elevated.
	if (( RESTART_TRIGGER_TS )); then
		first_tick="$(first_heartbeat_after "$RESTART_TRIGGER_TS")"
		if [[ -n "$first_tick" ]]; then
			first=(${=first_tick})
			lag="$(printf '%.1f' $(( ${first[2]} - RESTART_TRIGGER_TS )))"
			restart_log first-tick "$EPOCHREALTIME" "$RESTART_TRIGGER_TS" \
				"$(btt_pid)" "${first[1]}" "tick_ts=${first[2]} lag=${lag}s"
			log "first heartbeat tick after restart: ${first[1]} ${lag}s after trigger (tick_ts=${first[2]})"
			RESTART_TRIGGER_TS=0
		fi
	fi

	# Halfway to the timeout, ask for a tick rather than waiting to accuse.
	if (( beat > HEARTBEAT_TIMEOUT / 2 )); then
		if (( ! nudged )); then
			log "heartbeat quiet for ${beat}s -- refreshing the timer widget"
			nudge_heartbeat
			nudged=1
		fi
		continue
	fi
	nudged=0

	lyrics="$(lyrics_display_state)"
	lyrics_cache="$(lyrics_cache_state)"
	if [[ "$lyrics_cache" == "mismatch" ]]; then
		log_verdict lyrics-cache-mismatch \
			"lyrics cache mismatch -- value=${LYRICS_VALUE_FILE} last=${LYRICS_LAST_FILE}"
	fi
	case "$lyrics" in
		overdue*|stalled*)
			(( overdue_strikes++ ))
			if (( overdue_strikes < LYRICS_OVERDUE_STRIKES )); then
				log_verdict "strike-${overdue_strikes}" \
					"lyrics display ${lyrics} -- strike ${overdue_strikes}/${LYRICS_OVERDUE_STRIKES}"
				continue
			fi
			log "lyrics display ${lyrics} while heartbeat is ${beat}s old -- restarting"
			overdue_strikes=0
			LAST_VERDICT=""
			restart_btt "lyrics-${lyrics%% *}" \
				"lyrics ${lyrics} while heartbeat is ${beat}s old"
			;;
		sampler-stale*|widget-locked*)
			# BTT is dispatching ticks, so the widget is running and simply has
			# nothing new to draw: Apple Music, the detached sampler, or the
			# widget's own lock is the one that is stuck. A restart cannot fix
			# any of those, and would cost a Touch Bar outage for nothing.
			overdue_strikes=0
			DEFER_SINCE=0
			log_verdict "${lyrics%% *}" "lyrics ${lyrics} -- not BTT (heartbeat ${beat}s)"
			;;
		*)
			overdue_strikes=0
			DEFER_SINCE=0
			log_verdict "healthy-${lyrics%% *}" "healthy -- heartbeat=${beat}s lyrics=${lyrics}"
			;;
	esac
done

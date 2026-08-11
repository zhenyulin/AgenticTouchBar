#!/usr/bin/env zsh
#
# Records a manual BetterTouchTool restart (the Date/Time widget tap).
#
# The Date/Time widget in bttpreset/Default.bttpreset runs this script as
# its first action (BTTOrder 5), then BTT's native "Restart BetterTouchTool"
# action (BTTOrder 7) does the actual restart. Every write here happens
# before that restart, and the ledger row goes first: BTT may kill this
# process the moment the restart action runs.
#
# Why this exists: freeze-guard restarts are fully logged -- `before` and
# `first-tick` rows in logs/restart.tsv, `+` markers in the traces, lines in
# logs/freeze-guard.log -- but a manual tap restart was invisible to every
# ledger. When the user gives up on a stuck Touch Bar and restarts by hand,
# that moment is the strongest evidence about what the guard did or did not
# see. So it is recorded here, together with the guard's own last assessment
# (logs/freeze-guard-state.json, rewritten every probe). A missing or stale
# state file is recorded as such -- it is itself an answer to "why didn't
# the guard catch this earlier?".
#
# The guard closes this row with a `manual-first-tick` row the moment the
# first heartbeat tick lands after the tap, giving manual restarts the same
# recovery-lag evidence as its own.
#
# The helpers below mirror btt-freeze-guard.sh and are kept in sync by hand;
# the guard is not sourceable (launchd runs it standalone).
#

set -u

zmodload zsh/datetime

REPO_DIR="${BTT_REPO_DIR:-$HOME/Documents/BTT}"
LOG_DIR="${BTT_LOG_DIR:-$REPO_DIR/logs}"
LOG="$LOG_DIR/freeze-guard.log"
RESTART_LOG="${BTT_RESTART_LOG:-$LOG_DIR/restart.tsv}"
WIDGET_TRACE_FILE="${BTT_WIDGET_TRACE_FILE:-$LOG_DIR/trace.tsv}"
LYRICS_TRACE_FILE="${BTT_LYRICS_TRACE_FILE:-$LOG_DIR/lyrics/trace.tsv}"
# The guard's per-probe assessment; see write_state() in btt-freeze-guard.sh.
STATE_FILE="${BTT_FREEZE_GUARD_STATE_FILE:-$LOG_DIR/freeze-guard-state.json}"
PROBE_INTERVAL="${BTT_PROBE_INTERVAL:-10}"

mkdir -p "$LOG_DIR" 2>/dev/null

log() {
	echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"
}

# --- Mirrors of btt-freeze-guard.sh (kept in sync by hand) ------------------

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

# Seconds spanned by the ticks since the last `+` marker row.
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

# One row of logs/restart.tsv, header created on first use. Same schema as
# the guard's restart_log, so both writers share one ledger.
restart_log() {
	local phase="$1" ts="$2" restart_id="$3" pid="$4" key="$5" detail="$6"
	[[ -f "$RESTART_LOG" ]] || \
		printf 'phase\tts\trestart_id\tbtt_pid\tkey\tdetail\n' > "$RESTART_LOG" 2>/dev/null || true
	printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
		"$phase" "$ts" "$restart_id" "$pid" "$key" "$detail" \
		>> "$RESTART_LOG" 2>/dev/null || true
}

btt_pid() {
	/usr/bin/pgrep -x BetterTouchTool 2>/dev/null | /usr/bin/awk 'NR == 1 { print; exit }'
}

# --- The guard's view at this moment ----------------------------------------

# One of: missing / stale (…s old) / ok heartbeat=…s lyrics=… verdict=…
guard_state() {
	local state_ts age heartbeat lyrics verdict
	[[ -f "$STATE_FILE" ]] || { print -r -- missing; return }
	state_ts="$(json_number "$STATE_FILE" ts)"
	[[ -n "$state_ts" ]] || { print -r -- missing; return }
	age=$(( EPOCHREALTIME - state_ts ))
	if (( age > PROBE_INTERVAL * 3 )); then
		print -r -- "stale ts=${state_ts} age=${age%.*}s"
		return
	fi
	heartbeat="$(json_number "$STATE_FILE" heartbeat_age)"
	lyrics="$(json_string "$STATE_FILE" lyrics)"
	verdict="$(json_string "$STATE_FILE" verdict)"
	printf 'ok heartbeat=%ss lyrics=%s verdict=%s\n' \
		"${heartbeat:-unknown}" "${lyrics:-unknown}" "${verdict:-unknown}"
}

# --- Record the manual restart ----------------------------------------------

state="$(guard_state)"
restart_id="$EPOCHREALTIME"

# The ledger row first: the restart action follows immediately and may kill
# this process before any later write lands.
restart_log manual-before "$EPOCHREALTIME" "$restart_id" "$(btt_pid)" \
	date-time-tap "guard=${state}"

# The same `+` markers the guard writes, so a manual restart is visible in
# the traces and the next restart's run-duration bookkeeping does not span
# across it.
mkdir -p "${WIDGET_TRACE_FILE:h}" "${LYRICS_TRACE_FILE:h}" 2>/dev/null || true
printf '+\t%s\tBTT manual restart\n' "$(restart_duration "$WIDGET_TRACE_FILE")" \
	>> "$WIDGET_TRACE_FILE" 2>/dev/null || true
printf '+\t%s\tBTT manual restart\n' "$(restart_duration "$LYRICS_TRACE_FILE")" \
	>> "$LYRICS_TRACE_FILE" 2>/dev/null || true

log "manual restart requested via Date/Time widget tap -- guard=${state} (restart_id=${restart_id})"

#!/usr/bin/env zsh
#
# Records a manual BetterTouchTool restart (the Date/Time widget tap).
#
# The Date/Time widget in bttpreset/Default.bttpreset runs this script as
# its first action (BTTOrder 5), then BTT's native "Restart BetterTouchTool"
# action (BTTOrder 7) does the actual restart. Every write here happens
# before that restart: BTT may kill this process the moment the restart
# action runs.
#
# What is left to record is the restart itself. The trace markers are read
# by the widget traces themselves, and without one a restart silently
# inflates the next run-duration measurement.
#

set -u

zmodload zsh/datetime

# Shared with actions/btt-quit.sh: one log, and one way to find BTT's pid.
source "${0:A:h}/lib/btt-control.sh"

LOG_DIR="$BTT_CONTROL_LOG_DIR"
WIDGET_TRACE_FILE="${BTT_WIDGET_TRACE_FILE:-$LOG_DIR/trace.tsv}"
LYRICS_TRACE_FILE="${BTT_LYRICS_TRACE_FILE:-$LOG_DIR/lyrics/trace.tsv}"

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

# --- Record the manual restart ----------------------------------------------

restart_id="$EPOCHREALTIME"

# A `+` marker in each trace, so a manual restart is visible there and the
# next run-duration measurement does not span across it.
mkdir -p "${WIDGET_TRACE_FILE:h}" "${LYRICS_TRACE_FILE:h}" 2>/dev/null || true
printf '+\t%s\tBTT manual restart\n' "$(restart_duration "$WIDGET_TRACE_FILE")" \
	>> "$WIDGET_TRACE_FILE" 2>/dev/null || true
printf '+\t%s\tBTT manual restart\n' "$(restart_duration "$LYRICS_TRACE_FILE")" \
	>> "$LYRICS_TRACE_FILE" 2>/dev/null || true

btt_control_log "manual restart requested via Date/Time widget tap -- btt_pid=$(btt_control_pid) (restart_id=${restart_id})"

#!/usr/bin/env zsh

#
# Shared support for the manual BetterTouchTool control actions -- the Date/Time
# widget's tap (tap-restart.sh) and long-press (btt-quit.sh).
#
# One log for both, because the question they are kept for is a single
# timeline: was this restart something I asked for, or did BTT go down on its
# own? Splitting that across two files would only make it harder to read.
#

BTT_CONTROL_REPO_DIR="${BTT_REPO_DIR:-$HOME/Documents/BTT}"
BTT_CONTROL_LOG_DIR="${BTT_LOG_DIR:-$BTT_CONTROL_REPO_DIR/logs}"
BTT_CONTROL_LOG="$BTT_CONTROL_LOG_DIR/btt-control.log"

mkdir -p "$BTT_CONTROL_LOG_DIR" 2>/dev/null || true

btt_control_log() {
	print -r -- "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$BTT_CONTROL_LOG" 2>/dev/null || true
}

# BTT's process id, or nothing when it is not running.
btt_control_pid() {
	/usr/bin/pgrep -x BetterTouchTool 2>/dev/null | /usr/bin/awk 'NR == 1 { print; exit }'
}

#!/usr/bin/env zsh
#
# Manual BetterTouchTool quit helper -- makes quitting BTT stick.
#
# Usage:
#   btt-quit.sh
#
# Why this exists: BTT's own quit only works while its main thread is
# healthy. The AppKit layout bug this repo tracks (see btt-freeze-guard.sh)
# leaves BTT alive but unable to process the quit Apple Event, and BTT's own
# relauncher, BTTRelaunch, brings the process back whenever it dies without
# a graceful quit. The result is that quitting a wedged BTT never sticks --
# either the freeze-guard restarts the still-alive process 90s later, or
# force-quitting it lets BTTRelaunch resurrect it.
#
# So quitting BTT from here does three things, in order:
#   1. Kill BTTRelaunch -- the only thing that can resurrect BTT after death.
#   2. Ask BTT to quit gracefully and wait a short, bounded time.
#   3. If it is still alive (wedged mid-quit), SIGTERM and then SIGKILL.
#
# After this BTT is fully dead, and the freeze-guard's existing "BTT is not
# running -- respecting quit" path keeps it dead: no restart, no marker, no
# change to freeze protection. The next launch of BTT re-creates BTTRelaunch
# and the guard watches over the new process as before.
#
# Timings are bounded exactly like the freeze-guard's own restart sequence,
# so a wedged BTT costs a few seconds, not a stuck terminal.
#

set -u

REPO_DIR="${BTT_REPO_DIR:-$HOME/Documents/BTT}"
LOG_DIR="${BTT_LOG_DIR:-$REPO_DIR/logs}"
LOG="$LOG_DIR/freeze-guard.log"

# How long the graceful-quit Apple Event may take before SIGTERM.
GRACEFUL_QUIT_WAIT="${BTT_QUIT_GRACEFUL_WAIT:-5}"
# How long after SIGTERM before SIGKILL.
TERM_WAIT="${BTT_QUIT_TERM_WAIT:-3}"

number_or() {
	local value="$1" fallback="$2"
	[[ "$value" =~ ^[0-9]+([.][0-9]+)?$ ]] || value="$fallback"
	(( value > 0 )) || value="$fallback"
	print -r -- "$value"
}

GRACEFUL_QUIT_WAIT="$(number_or "$GRACEFUL_QUIT_WAIT" 5)"
TERM_WAIT="$(number_or "$TERM_WAIT" 3)"

log() {
	echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG" 2>/dev/null || true
}

btt_pid() {
	/usr/bin/pgrep -x BetterTouchTool 2>/dev/null | /usr/bin/awk 'NR == 1 { print; exit }'
}

btt_still_running() {
	kill -0 "$1" 2>/dev/null
}

mkdir -p "$LOG_DIR" 2>/dev/null || true
log "manual quit requested"

# --- 1. Kill the relauncher, so nothing can resurrect BTT -------------------

relaunch_pids="$(/usr/bin/pgrep -x BTTRelaunch 2>/dev/null || true)"
if [[ -n "$relaunch_pids" ]]; then
	log "manual quit -- killing BTTRelaunch (pids: ${relaunch_pids//$'\n'/ })"
	print -r -- "${relaunch_pids//$'\n'/ }" | /usr/bin/xargs /bin/kill 2>/dev/null || true
	for (( waited = 0; waited < 8; waited++ )); do
		/usr/bin/pgrep -x BTTRelaunch >/dev/null 2>&1 || break
		sleep 0.25
	done
	remaining="$(/usr/bin/pgrep -x BTTRelaunch 2>/dev/null || true)"
	if [[ -n "$remaining" ]]; then
		log "manual quit -- BTTRelaunch ignored SIGTERM -- SIGKILL"
		print -r -- "${remaining//$'\n'/ }" | /usr/bin/xargs /bin/kill -KILL 2>/dev/null || true
	fi
fi

# --- 2+3. Quit BTT: graceful Apple Event, then TERM, then KILL --------------

pid="$(btt_pid)"
if [[ -z "$pid" ]]; then
	log "manual quit -- BTT was not running"
	print -r -- "BTT was not running."
	exit 0
fi

log "manual quit -- asking BTT (pid=${pid}) to quit gracefully"
/usr/bin/osascript -e 'tell application "BetterTouchTool" to quit' >/dev/null 2>&1 &
request_pid=$!
for (( waited = 0; waited < GRACEFUL_QUIT_WAIT * 4; waited++ )); do
	if ! btt_still_running "$pid"; then
		kill "$request_pid" 2>/dev/null || true
		wait "$request_pid" 2>/dev/null || true
		log "manual quit -- BTT exited gracefully (pid=${pid})"
		print -r -- "BTT quit (pid=${pid})."
		exit 0
	fi
	sleep 0.25
done
kill "$request_pid" 2>/dev/null || true
wait "$request_pid" 2>/dev/null || true

log "manual quit -- graceful quit timed out after ${GRACEFUL_QUIT_WAIT}s (pid=${pid}) -- SIGTERM"
kill "$pid" 2>/dev/null || true
for (( waited = 0; waited < TERM_WAIT * 4; waited++ )); do
	if ! btt_still_running "$pid"; then
		log "manual quit -- BTT exited after SIGTERM (pid=${pid})"
		print -r -- "BTT quit (pid=${pid})."
		exit 0
	fi
	sleep 0.25
done

log "manual quit -- BTT ignored SIGTERM for ${TERM_WAIT}s (pid=${pid}) -- SIGKILL"
kill -KILL "$pid" 2>/dev/null || true
for (( waited = 0; waited < 8; waited++ )); do
	btt_still_running "$pid" || break
	sleep 0.25
done
if btt_still_running "$pid"; then
	log "manual quit -- BTT pid ${pid} did not exit after SIGKILL"
	print -r -- "BTT pid ${pid} still alive after SIGKILL." >&2
	exit 1
fi
log "manual quit -- BTT force-quit (pid=${pid})"
print -r -- "BTT force-quit (pid=${pid})."

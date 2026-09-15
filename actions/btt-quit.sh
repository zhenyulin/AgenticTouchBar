#!/usr/bin/env zsh
#
# Manual BetterTouchTool quit helper -- makes quitting BTT stick.
#
# Usage:
#   btt-quit.sh
#
# Why this exists: BTT's own quit only works while its main thread is
# healthy. A wedged main thread (see specs/CONSTRAINTS.md, Cause B) leaves
# BTT alive but unable to process the quit Apple Event, and BTT's own
# relauncher, BTTRelaunch, brings the process back whenever it dies without
# a graceful quit. The result is that quitting a wedged BTT never sticks:
# force-quitting it just lets BTTRelaunch resurrect it.
#
# So quitting BTT from here does three things, in order:
#   1. Kill BTTRelaunch -- the only thing that can resurrect BTT after death.
#   2. Ask BTT to quit gracefully and wait a short, bounded time.
#   3. If it is still alive (wedged mid-quit), SIGTERM and then SIGKILL.
#
# After this BTT is fully dead and stays dead. The next launch of BTT
# re-creates BTTRelaunch.
#
# Every wait here is bounded, so a wedged BTT costs a few seconds rather
# than a stuck terminal.
#

set -u

# Shared with actions/tap-restart.sh: one log, and one way to find BTT's pid.
source "${0:A:h}/lib/btt-control.sh"

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

btt_still_running() {
	kill -0 "$1" 2>/dev/null
}

btt_control_log "manual quit requested"

# --- 1. Kill the relauncher, so nothing can resurrect BTT -------------------

relaunch_pids="$(/usr/bin/pgrep -x BTTRelaunch 2>/dev/null || true)"
if [[ -n "$relaunch_pids" ]]; then
	btt_control_log "manual quit -- killing BTTRelaunch (pids: ${relaunch_pids//$'\n'/ })"
	print -r -- "${relaunch_pids//$'\n'/ }" | /usr/bin/xargs /bin/kill 2>/dev/null || true
	for (( waited = 0; waited < 8; waited++ )); do
		/usr/bin/pgrep -x BTTRelaunch >/dev/null 2>&1 || break
		sleep 0.25
	done
	remaining="$(/usr/bin/pgrep -x BTTRelaunch 2>/dev/null || true)"
	if [[ -n "$remaining" ]]; then
		btt_control_log "manual quit -- BTTRelaunch ignored SIGTERM -- SIGKILL"
		print -r -- "${remaining//$'\n'/ }" | /usr/bin/xargs /bin/kill -KILL 2>/dev/null || true
	fi
fi

# --- 2+3. Quit BTT: graceful Apple Event, then TERM, then KILL --------------

pid="$(btt_control_pid)"
if [[ -z "$pid" ]]; then
	btt_control_log "manual quit -- BTT was not running"
	print -r -- "BTT was not running."
	exit 0
fi

btt_control_log "manual quit -- asking BTT (pid=${pid}) to quit gracefully"
/usr/bin/osascript -e 'tell application "BetterTouchTool" to quit' >/dev/null 2>&1 &
request_pid=$!
for (( waited = 0; waited < GRACEFUL_QUIT_WAIT * 4; waited++ )); do
	if ! btt_still_running "$pid"; then
		kill "$request_pid" 2>/dev/null || true
		wait "$request_pid" 2>/dev/null || true
		btt_control_log "manual quit -- BTT exited gracefully (pid=${pid})"
		print -r -- "BTT quit (pid=${pid})."
		exit 0
	fi
	sleep 0.25
done
kill "$request_pid" 2>/dev/null || true
wait "$request_pid" 2>/dev/null || true

btt_control_log "manual quit -- graceful quit timed out after ${GRACEFUL_QUIT_WAIT}s (pid=${pid}) -- SIGTERM"
kill "$pid" 2>/dev/null || true
for (( waited = 0; waited < TERM_WAIT * 4; waited++ )); do
	if ! btt_still_running "$pid"; then
		btt_control_log "manual quit -- BTT exited after SIGTERM (pid=${pid})"
		print -r -- "BTT quit (pid=${pid})."
		exit 0
	fi
	sleep 0.25
done

btt_control_log "manual quit -- BTT ignored SIGTERM for ${TERM_WAIT}s (pid=${pid}) -- SIGKILL"
kill -KILL "$pid" 2>/dev/null || true
for (( waited = 0; waited < 8; waited++ )); do
	btt_still_running "$pid" || break
	sleep 0.25
done
if btt_still_running "$pid"; then
	btt_control_log "manual quit -- BTT pid ${pid} did not exit after SIGKILL"
	print -r -- "BTT pid ${pid} still alive after SIGKILL." >&2
	exit 1
fi
btt_control_log "manual quit -- BTT force-quit (pid=${pid})"
print -r -- "BTT force-quit (pid=${pid})."

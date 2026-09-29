#!/usr/bin/env zsh
# BetterTouchTool widget for the Codex 5 h / 7 d quotas.

set -u
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# Absolute path to this script: the detached refresh re-invokes it, and BTT
# may well have started it by a relative path.
SELF="${0:A}"
source "${SELF:h}/lib/btt-widget.sh"
btt_parse_widget_args "$@"

BTT_WIDGET_NAME="codex-quota"
BTT_WIDGET_REFRESH_MAX_RUN=180
# Codex exposes a five-hour rolling quota (primary) and a seven-day weekly
# quota (secondary). These bounds are unrelated to how often BTT redraws the
# widget.
BTT_WIDGET_QUOTA_RESET_CYCLE_MINUTES=300
BTT_WIDGET_QUOTA_SECONDARY_RESET_CYCLE_MINUTES=10080

VALUE_MAX_AGE="${CODEX_QUOTA_MAX_AGE:-300}"
CODEX_QUOTA_CLI="${CODEX_QUOTA_CLI:-$(command -v codex)}"
CODEX_QUOTA_TIMEOUT="${CODEX_QUOTA_TIMEOUT:-30}"

CODEX_QUOTA_RPC='
import json
import os
import select
import subprocess
import sys
import time

timeout = float(sys.argv[2])
process = subprocess.Popen(
	[sys.argv[1], "-s", "read-only", "-a", "never", "app-server"],
	stdin=subprocess.PIPE,
	stdout=subprocess.PIPE,
	text=True,
)

def send(message):
	process.stdin.write(json.dumps(message) + "\n")
	process.stdin.flush()

def receive(request_id):
	deadline = time.monotonic() + timeout
	pending = bytearray()
	while time.monotonic() < deadline:
		if b"\n" in pending:
			line, _, remainder = pending.partition(b"\n")
			pending = bytearray(remainder)
			message = json.loads(line)
			if message.get("id") == request_id:
				return message
			continue
		ready, _, _ = select.select([process.stdout], [], [], deadline - time.monotonic())
		if not ready:
			return None
		chunk = os.read(process.stdout.fileno(), 4096)
		if not chunk:
			return None
		pending.extend(chunk)
	return None

try:
	send({"method": "initialize", "id": 0, "params": {"clientInfo": {"name": "btt_quota_widget", "title": "BTT Quota Widget", "version": "1.0.0"}}})
	if (receive(0) or {}).get("error") is not None:
		raise RuntimeError("Codex app-server initialization failed")
	send({"method": "initialized", "params": {}})
	send({"method": "account/rateLimits/read", "id": 1, "params": {}})
	response = receive(1)
	if response is None or response.get("error") is not None:
		raise RuntimeError("Codex rate-limit request failed")
	result = response.get("result") or {}
	if not isinstance(result.get("rateLimits"), dict):
		raise RuntimeError("Codex rate-limit response had no rate limits")
	print(json.dumps(result))
finally:
	if process.poll() is None:
		process.terminate()
		try:
			process.wait(timeout=2)
		except subprocess.TimeoutExpired:
			process.kill()
			process.wait()
'

# Read quota windows through Codex CLI's app-server so it can use its own
# credential store, including the OS keychain when configured.
quota_codex_usage() {
	if [[ -z "$CODEX_QUOTA_CLI" || ! -x "$CODEX_QUOTA_CLI" ]]; then
		printf 'NO CODEX CLI'
		return 1
	fi

	local json
	json="$(
		/usr/bin/python3 -c "$CODEX_QUOTA_RPC" "$CODEX_QUOTA_CLI" "$CODEX_QUOTA_TIMEOUT" 2>>"$QUOTA_LOG" \
			| "$QUOTA_JQ" -c '
				def window($value):
					if ($value | type) == "object" then
						{usedPercent: $value.usedPercent,
						 resetsAt: (if ($value.resetsAt | type) == "number"
									then ($value.resetsAt | floor | todateiso8601)
									else null end)}
					else null end;

							{provider: "codex",
							 usage: {primary: window(.rateLimits.primary),
								 secondary: window(.rateLimits.secondary)}}
			' 2>>"$QUOTA_LOG"
	)"
	local jq_status=$?

	if (( jq_status != 0 )) || [[ -z "$json" ]]; then
		printf 'ERR'
		return 1
	fi

	printf '%s' "$json"
}

# Codex exposes quota windows under different names depending on the account.
QUOTA_FETCH="quota_codex_usage"
QUOTA_PROVIDER="codex"
QUOTA_WINDOW='.usage.primary // .usage.secondary // .usage.tertiary'
QUOTA_SECONDARY_WINDOW='.usage.secondary'
# Five-hour quota on the first row, weekly on the second.
QUOTA_ROWS='
	used($window.usedPercent) + " " + until_reset($window.resetsAt)
	+ "\n" + used($secondary.usedPercent) + " " + until_reset($secondary.resetsAt)
'

source "${SELF:h}/lib/quota-widget.sh"

quota_widget_main "$REFRESH_MODE" "$SELF" "$VALUE_MAX_AGE"

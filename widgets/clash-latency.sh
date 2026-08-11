#!/usr/bin/env zsh
# BetterTouchTool widget for the latency of the currently selected Clash node.

set -u
set -o pipefail
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

REFRESH_MODE=0
if [[ "${1:-}" == "--refresh" ]]; then REFRESH_MODE=1; shift; fi
if [[ -n "${1:-}" ]]; then BTT_WIDGET_UUID="$1"; shift; else BTT_WIDGET_UUID="${BTT_WIDGET_UUID:-}"; fi

SELF="${0:A}"
source "${SELF:h}/lib/btt-widget.sh"
source "${SELF:h}/lib/clash.sh"

BTT_WIDGET_NAME="clash-latency"
BTT_WIDGET_ICON="${CLASH_ICON:-}"
BTT_WIDGET_COLOR_OVERRIDE="${CLASH_FONT_COLOR:-}"
BTT_WIDGET_REFRESH_MAX_RUN=60
BTT_WIDGET_LATENCY_MIN_MS="${CLASH_LATENCY_MIN_MS:-150}"
BTT_WIDGET_LATENCY_MAX_MS="${CLASH_LATENCY_MAX_MS:-500}"
# Every measurement is appended to a history file, so a slow period can be
# reviewed afterwards; the shared lifecycle calls this once the refresh has
# stored its value.
BTT_WIDGET_REFRESH_HOOK=write_latency_history

LATENCY_HISTORY_FILE="${BTT_LATENCY_HISTORY_FILE:-${BTT_LOG_DIR:-$BTT_REPO_DIR/logs}/latency-history.tsv}"
VALUE_MAX_AGE="${CLASH_MAX_AGE:-30}"

TEST_URL="${CLASH_TEST_URL:-https://cp.cloudflare.com/generate_204}"
TIMEOUT_MS="${CLASH_TIMEOUT_MS:-3000}"

write_latency_history() {
    local stamp="$1" value="$2" latency node
    latency="${value%%$'\n'*}"
    [[ "$value" == *$'\n'* ]] && node="${value#*$'\n'}" || node=""
    latency="${latency//$'\t'/ }"
    node="${node//$'\t'/ }"
    node="${node//$'\n'/ }"
    mkdir -p "${LATENCY_HISTORY_FILE:h}" 2>/dev/null || return 0
    printf '%s\t%s\t%s\n' "$stamp" "$latency" "$node" >> "$LATENCY_HISTORY_FILE" 2>/dev/null || true
}

compute_value() {
    command -v curl >/dev/null 2>&1 || { printf 'No curl'; return; }
    command -v jq >/dev/null 2>&1 || { printf 'No jq'; return; }
    [[ "$TIMEOUT_MS" =~ ^[0-9]+$ ]] || TIMEOUT_MS=3000

    local node delay_json delay
    # Node resolution runs in a command-substitution subshell. Set these in
    # the current shell too, so the following delay request has the same
    # socket and authentication arguments.
    clash_setup_curl
    node="$(clash_resolve_node)" || { printf 'Controller'; return; }
    delay_json="$(curl -fsS --connect-timeout 1 --max-time 5 \
        "${CLASH_CURL_ARGS[@]}" "${CLASH_AUTH_HEADERS[@]}" \
        "$API/proxies/$(clash_uri_encode "$node")/delay?url=$(clash_uri_encode "$TEST_URL")&timeout=$TIMEOUT_MS")" || delay_json=""
    delay="$(jq -r '.delay // 0' <<< "$delay_json" 2>/dev/null || printf '0')"

    if [[ "$delay" =~ ^[0-9]+$ ]] && (( delay > 0 )); then
        # The second row's alignment is the shared lib's job: btt_publish
        # indents it whenever the first row opens with "1".
        printf '%sms\n%s' "$delay" "$(clash_proxy_label "$node")"
    else
        printf 'Timeout'
    fi
}

btt_cached_widget_main "$REFRESH_MODE" "$SELF" "$VALUE_MAX_AGE" compute_value

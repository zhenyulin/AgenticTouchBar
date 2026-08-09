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

STARTED="$(btt_now)"
ICON_PATH="${CLASH_ICON:-}"
BTT_WIDGET_ICON="$ICON_PATH"
BTT_WIDGET_NAME="clash-latency"
LATENCY_HISTORY_FILE="${BTT_LATENCY_HISTORY_FILE:-${BTT_LOG_DIR:-$BTT_REPO_DIR/logs}/latency-history.tsv}"
VALUE_MAX_AGE="${CLASH_MAX_AGE:-30}"
BTT_WIDGET_REFRESH_MAX_RUN=60
BTT_WIDGET_LATENCY_MIN_MS="${CLASH_LATENCY_MIN_MS:-150}"
BTT_WIDGET_LATENCY_MAX_MS="${CLASH_LATENCY_MAX_MS:-800}"

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

TEST_URL="${CLASH_TEST_URL:-https://cp.cloudflare.com/generate_204}"
TIMEOUT_MS="${CLASH_TIMEOUT_MS:-3000}"
FONT_COLOR="${CLASH_FONT_COLOR:-}"

emit_widget() {
    local text="$1"
    local color="${FONT_COLOR:-$(btt_current_color "$text")}"
    if [[ -n "$ICON_PATH" && -f "$ICON_PATH" ]]; then
        jq -cn --arg text "$text" --arg color "$color" --arg icon "$ICON_PATH" \
            '{text: $text, font_color: $color, icon_path: $icon}'
    else
        jq -cn --arg text "$text" --arg color "$color" \
            '{text: $text, font_color: $color}'
    fi
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
        printf '%sms\n %s' "$delay" "$(clash_proxy_label "$node")"
    else
        printf 'Timeout'
    fi
}

if (( REFRESH_MODE )); then
    REFRESHED="$(compute_value)"
    btt_cache_put "$BTT_WIDGET_NAME" "$REFRESHED"
    write_latency_history "$STARTED" "$REFRESHED"
    case "$REFRESHED" in ""|*ERR*|No\ *) REFRESH_OUTCOME=error ;; *) REFRESH_OUTCOME=ok ;; esac
    btt_trace refresh "$STARTED" "$REFRESH_OUTCOME" "value=$REFRESHED"
    exit 0
fi

VALUE="$(btt_cache_get "$BTT_WIDGET_NAME" "$VALUE_MAX_AGE")"
FRESH=$?
FORCE=0; btt_force_pending && FORCE=1
if (( FRESH != 0 || FORCE )); then
    btt_refresh_detached "$BTT_WIDGET_NAME" "$BTT_WIDGET_REFRESH_MAX_RUN" "$SELF" --refresh "$BTT_WIDGET_UUID"
fi
OUTCOME=cached; (( FRESH != 0 )) && OUTCOME=stale; (( FORCE )) && OUTCOME=forced; [[ -n "$VALUE" ]] || OUTCOME=empty
btt_trace widget "$STARTED" "$OUTCOME"
emit_widget "${VALUE:-…}"

#!/usr/bin/env zsh
# BetterTouchTool widget showing the selected Clash node's region and name.

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
BTT_WIDGET_NAME="clash-region"
VALUE_MAX_AGE="${CLASH_REGION_MAX_AGE:-30}"
BTT_WIDGET_REFRESH_MAX_RUN=30
FONT_COLOR="${CLASH_FONT_COLOR:-}"

emit_widget() {
    jq -cn --arg text "$1" --arg color "${FONT_COLOR:-$(btt_current_color)}" \
        '{text: $text, font_color: $color}'
}

compute_value() {
    command -v curl >/dev/null 2>&1 || { printf '🌐'; return; }
    command -v jq >/dev/null 2>&1 || { printf '🌐'; return; }
    local node
    node="$(clash_resolve_node)" || { printf '🌐'; return; }
    printf '%s' "$(clash_region_flag "$node")"
}

if (( REFRESH_MODE )); then
    REFRESHED="$(compute_value)"
    btt_cache_put "$BTT_WIDGET_NAME" "$REFRESHED"
    btt_trace refresh "$STARTED" ok "value=$REFRESHED"
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
emit_widget "${VALUE:-🌐}"

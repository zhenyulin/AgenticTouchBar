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

BTT_WIDGET_NAME="clash-region"
BTT_WIDGET_COLOR_OVERRIDE="${CLASH_FONT_COLOR:-}"
BTT_WIDGET_REFRESH_MAX_RUN=30
# The flag is the whole widget, so a run with nothing cached yet shows the
# generic globe rather than the shared ellipsis.
BTT_WIDGET_EMPTY_TEXT="🌐"

VALUE_MAX_AGE="${CLASH_REGION_MAX_AGE:-30}"

compute_value() {
    command -v curl >/dev/null 2>&1 || { printf '🌐'; return; }
    command -v jq >/dev/null 2>&1 || { printf '🌐'; return; }
    local node
    node="$(clash_resolve_node)" || { printf '🌐'; return; }
    printf '%s' "$(clash_region_flag "$node")"
}

btt_cached_widget_main "$REFRESH_MODE" "$SELF" "$VALUE_MAX_AGE" compute_value

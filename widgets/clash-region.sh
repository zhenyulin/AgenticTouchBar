#!/usr/bin/env zsh
# BetterTouchTool widget showing the selected Clash node's region and name.

set -u
set -o pipefail
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

SELF="${0:A}"
source "${SELF:h}/lib/btt-widget.sh"
source "${SELF:h}/lib/clash.sh"
btt_parse_widget_args "$@"

BTT_WIDGET_NAME="clash-region"
BTT_WIDGET_COLOR_OVERRIDE="${CLASH_FONT_COLOR:-}"
# The label is a country flag emoji: those glyphs ignore font_color's RGB,
# so the shared grey dim colour would not show on it at all. They still
# honour the alpha channel, so the dim frame fades the flag instead -- the
# same trick as the weather icon instance in weather.sh.
BTT_WIDGET_DIM_COLOR="255,255,255,140"
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
    clash_region_flag "$node"
}

btt_cached_widget_main "$REFRESH_MODE" "$SELF" "$VALUE_MAX_AGE" compute_value

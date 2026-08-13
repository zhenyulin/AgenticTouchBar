#!/usr/bin/env zsh
# BetterTouchTool widget for the OpenCode Go weekly quota.

set -u
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export HOME="${HOME:-/Users/zhenyulin}"

SELF="${0:A}"
source "${SELF:h}/lib/btt-widget.sh"
btt_parse_widget_args "$@"

BTT_WIDGET_NAME="opencode-quota"
BTT_WIDGET_REFRESH_MAX_RUN=180
BTT_WIDGET_QUOTA_RESET_CYCLE_MINUTES=10080

VALUE_MAX_AGE="${OPENCODE_QUOTA_MAX_AGE:-300}"

# OpenCode Go exposes a five-hour rolling quota (primary), a seven-day weekly
# quota (secondary) and a monthly quota (tertiary). This widget shows only the
# weekly one.
QUOTA_PROVIDER="opencodego"
QUOTA_WINDOW='.usage.secondary'

source "${SELF:h}/lib/quota-widget.sh"

quota_widget_main "$REFRESH_MODE" "$SELF" "$VALUE_MAX_AGE"

#!/usr/bin/env zsh
# BetterTouchTool widget for the Claude 5 h / 7 d quotas.

set -u
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export HOME="${HOME:-/Users/zhenyulin}"

SELF="${0:A}"
source "${SELF:h}/lib/btt-widget.sh"
btt_parse_widget_args "$@"

BTT_WIDGET_NAME="claude-quota"
BTT_WIDGET_REFRESH_MAX_RUN=180
# Claude exposes a five-hour primary quota and a seven-day secondary quota.
# These bounds are unrelated to how often BTT redraws the widget.
BTT_WIDGET_QUOTA_RESET_CYCLE_MINUTES=300
BTT_WIDGET_QUOTA_SECONDARY_RESET_CYCLE_MINUTES=10080

VALUE_MAX_AGE="${CLAUDE_QUOTA_MAX_AGE:-300}"

QUOTA_PROVIDER="claude"
QUOTA_WINDOW='.usage.primary'
QUOTA_SECONDARY_WINDOW='.usage.secondary'
# Five-hour quota on the first row, weekly on the second. The colour at an
# exhausted weekly quota is computed from the 7-day reset, not the 5-hour one.
QUOTA_ROWS='
    used($window.usedPercent) + " " + until_reset($window.resetsAt)
    + "\n" + used($secondary.usedPercent) + " " + until_reset($secondary.resetsAt)
'

source "${SELF:h}/lib/quota-widget.sh"

quota_widget_main "$REFRESH_MODE" "$SELF" "$VALUE_MAX_AGE"

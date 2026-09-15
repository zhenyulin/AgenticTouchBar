#!/usr/bin/env zsh
# BetterTouchTool widget for the OpenCode Go 5 h / 7 d quotas.

set -u
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

SELF="${0:A}"
source "${SELF:h}/lib/btt-widget.sh"
btt_parse_widget_args "$@"

BTT_WIDGET_NAME="opencode-quota"
BTT_WIDGET_REFRESH_MAX_RUN=180
# OpenCode Go exposes a five-hour rolling quota (primary), a seven-day weekly
# quota (secondary) and a monthly quota (tertiary). This widget shows the
# five-hour and weekly ones; when codexbar omits the five-hour window the
# weekly one repeats in its row. These bounds are unrelated to how often BTT
# redraws the widget.
BTT_WIDGET_QUOTA_RESET_CYCLE_MINUTES=300
BTT_WIDGET_QUOTA_SECONDARY_RESET_CYCLE_MINUTES=10080

VALUE_MAX_AGE="${OPENCODE_QUOTA_MAX_AGE:-300}"

QUOTA_PROVIDER="opencodego"
QUOTA_WINDOW='.usage.primary // .usage.secondary'
QUOTA_SECONDARY_WINDOW='.usage.secondary'
# Five-hour quota on the first row, weekly on the second.
QUOTA_ROWS='
    used($window.usedPercent) + " " + until_reset($window.resetsAt)
    + "\n" + used($secondary.usedPercent) + " " + until_reset($secondary.resetsAt)
'

source "${SELF:h}/lib/quota-widget.sh"

quota_widget_main "$REFRESH_MODE" "$SELF" "$VALUE_MAX_AGE"

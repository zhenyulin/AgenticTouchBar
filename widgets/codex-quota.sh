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

# Whichever window codexbar reports for the plan: Codex exposes these under
# different names depending on the account.
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

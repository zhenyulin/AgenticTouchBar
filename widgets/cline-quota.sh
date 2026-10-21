#!/usr/bin/env zsh
# BetterTouchTool widget for ClinePass quota windows.

set -u
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

SELF="${0:A}"
source "${SELF:h}/lib/btt-widget.sh"
btt_parse_widget_args "$@"
# Repo-local secrets (CLINE_API_KEY / CLINEPASS_API_KEY live here): plain
# KEY=value lines, shell-quoted if the value contains spaces. Sourced ahead of
# the CLINE_QUOTA_* lookups below, so an .env entry can set those as well.
[[ -f "$BTT_REPO_DIR/.env" ]] && source "$BTT_REPO_DIR/.env"

BTT_WIDGET_NAME="cline-quota"
BTT_WIDGET_REFRESH_MAX_RUN=60
# ClinePass exposes a five-hour quota (primary), a weekly quota (secondary)
# and a monthly quota (tertiary). This widget draws the five-hour one on the
# first row and, on the second, whichever of the weekly and monthly ones has
# less left: the long window is what eventually stops the account, and the
# more-used of the two hides neither. These bounds are unrelated to how often
# BTT redraws the widget.
BTT_WIDGET_QUOTA_RESET_CYCLE_MINUTES=300
BTT_WIDGET_QUOTA_SECONDARY_RESET_CYCLE_MINUTES=10080
VALUE_MAX_AGE="${CLINE_QUOTA_MAX_AGE:-300}"
CLINE_QUOTA_TIMEOUT="${CLINE_QUOTA_TIMEOUT:-15}"
CLINE_QUOTA_USAGE_URL="${CLINE_QUOTA_USAGE_URL:-https://api.cline.bot/api/v1/users/me/plan/usage-limits}"
CLINE_QUOTA_SETTINGS_FILE="${CLINE_PROVIDER_SETTINGS_PATH:-${CLINE_DATA_DIR:+$CLINE_DATA_DIR/settings/providers.json}}"
CLINE_QUOTA_SETTINGS_FILE="${CLINE_QUOTA_SETTINGS_FILE:-${CLINE_DIR:+$CLINE_DIR/data/settings/providers.json}}"
CLINE_QUOTA_SETTINGS_FILE="${CLINE_QUOTA_SETTINGS_FILE:-$HOME/.cline/data/settings/providers.json}"
CLINE_QUOTA_LOG="${BTT_LOG_DIR:-${BTT_REPO_DIR:-$HOME/Documents/BTT}/logs}/cline-quota.log"

cline_quota_token() {
    if [[ -n "${CLINE_API_KEY:-}" ]]; then
        printf '%s' "$CLINE_API_KEY"
        return 0
    fi
    if [[ -n "${CLINEPASS_API_KEY:-}" ]]; then
        printf '%s' "$CLINEPASS_API_KEY"
        return 0
    fi

    [[ -r "$CLINE_QUOTA_SETTINGS_FILE" ]] || return 1
    "$QUOTA_JQ" -r '
        .providers.cline.settings as $settings
        | ($settings.auth.accessToken // $settings.apiKey // $settings.auth.apiKey // empty)
        | select(type == "string" and length > 0)
    ' "$CLINE_QUOTA_SETTINGS_FILE" 2>/dev/null
}

cline_quota_usage() {
    local token response code body
    token="$(cline_quota_token)"
    if [[ -z "$token" ]]; then
        printf 'NO KEY'
        return 1
    fi

    response="$(curl -sS -m "$CLINE_QUOTA_TIMEOUT" -w $'\n%{http_code}' \
        -H "Authorization: Bearer $token" \
        "$CLINE_QUOTA_USAGE_URL" 2>>"$CLINE_QUOTA_LOG")"
    code="${response##*$'\n'}"
    body="${response%$'\n'*}"
    if [[ "$code" == "401" || "$code" == "403" ]]; then
        printf 'RUN CLINE AUTH'
        return 1
    fi
    if [[ "$code" == "429" ]]; then
        printf 'RATE LIMIT'
        return 1
    fi
    if [[ "$code" != "200" || -z "$body" ]]; then
        printf 'API ERR'
        return 1
    fi

    local json
    json="$(printf '%s' "$body" | "$QUOTA_JQ" -ce '
        if .success != true or (.data.limits | type) != "array" then error("invalid ClinePass response") else .data.limits end
        | map(select(.type == "five_hour" or .type == "weekly" or .type == "monthly")
              | select((.percentUsed | type) == "number")
              | {key: .type, value: {usedPercent: .percentUsed,
                  resetsAt: ((.resetsAt // null) | if type == "string" then sub("\\.[0-9]+Z$"; "Z") else null end)}})
        | from_entries as $limits
        | {provider: "clinepass", usage: {
            primary: $limits.five_hour,
            secondary: $limits.weekly,
            tertiary: $limits.monthly
          }}
    ' 2>>"$CLINE_QUOTA_LOG")"
    local jq_status=$?
    if (( jq_status != 0 )) || [[ -z "$json" ]]; then
        printf 'PARSE ERR'
        return 1
    fi

    printf '%s' "$json"
}

QUOTA_FETCH="cline_quota_usage"
QUOTA_PROVIDER="clinepass"
QUOTA_WINDOW='.usage.primary'
# The second row reports the more-used long window, so a monthly quota that has
# run further than the weekly one is what the row shows. A window the API omits
# compares as -1 and loses to the one it did send.
QUOTA_SECONDARY_WINDOW='.usage | if ((.tertiary.usedPercent // -1) > (.secondary.usedPercent // -1)) then .tertiary else .secondary end'
# Five-hour quota on the first row, the more-used long window on the second.
QUOTA_ROWS='
    used($window.usedPercent) + " " + until_reset($window.resetsAt)
    + "\n" + used($secondary.usedPercent) + " " + until_reset($secondary.resetsAt)
'

source "${SELF:h}/lib/quota-widget.sh"

quota_widget_main "$REFRESH_MODE" "$SELF" "$VALUE_MAX_AGE"

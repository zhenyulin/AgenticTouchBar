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
# five-hour and weekly ones; when the API omits the rolling window the weekly
# one repeats in its row. These bounds are unrelated to how often BTT redraws
# the widget.
BTT_WIDGET_QUOTA_RESET_CYCLE_MINUTES=300
BTT_WIDGET_QUOTA_SECONDARY_RESET_CYCLE_MINUTES=10080

VALUE_MAX_AGE="${OPENCODE_QUOTA_MAX_AGE:-300}"

# The key `opencode auth login` stored, which this endpoint answers to. An
# explicit OPENCODE_QUOTA_API_KEY wins, so the widget also works without the CLI.
OPENCODE_API_KEY="${OPENCODE_QUOTA_API_KEY:-}"
OPENCODE_AUTH_FILE="${OPENCODE_QUOTA_AUTH_FILE:-$HOME/.local/share/opencode/auth.json}"
OPENCODE_USAGE_TIMEOUT="${OPENCODE_QUOTA_TIMEOUT:-15}"
OPENCODE_USAGE_URL="${OPENCODE_QUOTA_USAGE_URL:-https://opencode.ai/zen/go/v1/usage}"

# codexbar cannot see this plan's usage. `--source auto` resolves to its local
# strategy, which derives the windows from the OpenCode CLI's own message
# history, so usage spent through another client -- or by `opencode serve` --
# reads as none of the quota used; its `web` strategy wants an opencode.ai
# session cookie. The plan's own usage API knows, and the CLI's login already
# authorises it, so fetch that instead of asking codexbar.
OPENCODE_NORMALIZE='
    def window($name):
        .usage[$name]
        | if type == "object" and (.percent | type) == "number" then
            {usedPercent: .percent,
             resetsAt: ((.resetsAt // null)
                        | if type == "string" then sub("\\.[0-9]+"; "") else null end)}
          else
            null
          end;

    {provider: "opencodego",
     usage: {primary: window("rolling"),
             secondary: window("weekly"),
             tertiary: window("monthly")}}
'

# Both the "3h" label and the stored reset epoch go through jq's
# fromdateiso8601, which rejects the fractional seconds this API sends.
quota_opencodego_key() {
    if [[ -n "$OPENCODE_API_KEY" ]]; then
        printf '%s' "$OPENCODE_API_KEY"
        return 0
    fi

    [[ -r "$OPENCODE_AUTH_FILE" ]] || return 1

    "$QUOTA_JQ" -r '.["opencode-go"].key // empty' "$OPENCODE_AUTH_FILE" 2>/dev/null
}

quota_opencodego_usage() {
    local key
    key="$(quota_opencodego_key)"
    if [[ -z "$key" ]]; then
        printf 'NO KEY'
        return 1
    fi

    # -w appends the status after the body, so a key the plan rejects is told
    # apart from an endpoint that never answered: both arrive with no body.
    local response code body
    response="$(curl -sS -m "$OPENCODE_USAGE_TIMEOUT" -w $'\n%{http_code}' \
        -H "Authorization: Bearer $key" \
        "$OPENCODE_USAGE_URL" 2>>"$QUOTA_LOG")"
    code="${response##*$'\n'}"
    body="${response%$'\n'*}"

    if [[ "$code" == "401" || "$code" == "403" ]]; then
        printf 'AUTH ERR'
        return 1
    fi

    # Anything else with no body -- refused, unreachable, timed out -- has no
    # usage to read; a body that is not a usage record fails in jq below.
    if [[ -z "$body" ]]; then
        printf 'EMPTY JSON'
        return 1
    fi

    local json
    json="$(printf '%s' "$body" | "$QUOTA_JQ" -c "$OPENCODE_NORMALIZE" 2>>"$QUOTA_LOG")"
    local jq_status=$?

    if (( jq_status != 0 )) || [[ -z "$json" ]]; then
        printf 'ERR'
        return 1
    fi

    printf '%s' "$json"
}

QUOTA_FETCH="quota_opencodego_usage"
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

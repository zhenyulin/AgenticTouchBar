#!/bin/zsh

export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
export HOME="${HOME:-/Users/zhenyulin}"

# Refresh mode runs the slow half of this widget, detached from the widget
# path below. See claude-quota.sh for why anything touching the network must
# stay off a BTT widget's own path.
REFRESH_MODE=0
if [[ "${1:-}" == "--refresh" ]]; then
    REFRESH_MODE=1
    shift
fi

# Optional first argument: BTT widget UUID.
if [[ -n "${1:-}" ]]; then
    BTT_WIDGET_UUID="$1"
    shift
else
    BTT_WIDGET_UUID="${BTT_WIDGET_UUID:-}"
fi

# Absolute path to this script: the detached refresh below re-invokes it,
# and BTT may well have started it by a relative path.
SELF="${0:A}"

source "${SELF:h}/lib/btt-widget.sh"

BTT_WIDGET_NAME="codex-quota"

VALUE_MAX_AGE="${CODEX_QUOTA_MAX_AGE:-300}"
BTT_WIDGET_REFRESH_MAX_RUN=180
BTT_WIDGET_QUOTA_RESET_CYCLE_MINUTES=10080

REPO_DIR="${BTT_REPO_DIR:-$HOME/Documents/BTT}"
LOG_DIR="${BTT_LOG_DIR:-$REPO_DIR/logs}"
LOG="$LOG_DIR/btt-codexbar.log"

compute_value() {
    cd "$HOME" || {
        printf 'HOME ERR'
        return 0
    }

    local codexbar jq
    codexbar="$(command -v codexbar)"
    jq="$(command -v jq)"

    mkdir -p "$LOG_DIR"

    if [[ -z "$codexbar" || ! -x "$codexbar" ]]; then
        printf 'NO CODEXBAR'
        return 0
    fi

    if [[ -z "$jq" || ! -x "$jq" ]]; then
        printf 'NO JQ'
        return 0
    fi

    local json
    json="$(
        "$codexbar" \
            usage \
            --provider codex \
            --source auto \
            --format json \
            2>>"$LOG"
    )"

    if [[ -z "$json" ]]; then
        printf 'EMPTY JSON'
        return 0
    fi

    local reset_at
    reset_at="$(
        printf '%s' "$json" | "$jq" -r '
            (if type == "array" then . else [.] end)
            | map(
                select(.provider == "codex" and .usage != null)
                | (.usage.primary // .usage.secondary // .usage.tertiary)
                | select(. != null)
                | .resetsAt
                | if . == null then empty else fromdateiso8601 end
            )
            | first // empty
        ' 2>>"$LOG"
    )"
    local reset_status=$?
    if (( reset_status == 0 )); then
        btt_quota_reset_put "$BTT_WIDGET_NAME" "$reset_at"
    fi

    local text
    text="$(
        printf '%s' "$json" | "$jq" -r '
            def used($value):
                if $value == null then "—"
                else ($value | round | tostring) + "%"
                end;

            def until_reset($reset_at):
                if $reset_at == null then
                    "—"
                else
                    ([$reset_at | fromdateiso8601 - now | floor, 0] | max) as $seconds
                    | if $seconds >= 86400 then
                        (($seconds / 86400) | floor | tostring) + "d"
                      elif $seconds >= 3600 then
                        (($seconds / 3600) | floor | tostring) + "h"
                      elif $seconds >= 60 then
                        (($seconds / 60) | floor | tostring) + "m"
                      else
                        "<1m"
                      end
                end;

            (if type == "array" then . else [.] end)
            | map(
                select(.provider == "codex" and .usage != null)
                | (.usage.primary // .usage.secondary // .usage.tertiary) as $window
                | select($window != null)
                | used($window.usedPercent)
                + "\n" + until_reset($window.resetsAt)
            )
            | first // empty
        ' 2>>"$LOG"
    )"
    # Not `status`: zsh reserves that name as a read-only alias for $?.
    local jq_status=$?

    if (( jq_status != 0 )) || [[ -z "$text" ]]; then
        printf 'ERR'
        return 0
    fi

    printf '%s' "$text"
}

btt_cached_widget_main "$REFRESH_MODE" "$SELF" "$VALUE_MAX_AGE" compute_value

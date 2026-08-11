#!/bin/zsh

export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
export HOME="${HOME:-/Users/zhenyulin}"

# Refresh mode runs the slow half of this widget, detached from the widget
# path below. codexbar's Claude lookup takes the best part of a minute, and
# BTT runs every shell widget through one XPC service, so doing it inline
# freezes every other widget -- and every tap-refresh -- for that long.
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

source "$HOME/Documents/BTT/widgets/lib/btt-widget.sh"

BTT_WIDGET_NAME="claude-quota"

# Claude exposes a five-hour primary quota and a seven-day secondary quota.
# These bounds are unrelated to how often BTT redraws the widget.
VALUE_MAX_AGE="${CLAUDE_QUOTA_MAX_AGE:-300}"
BTT_WIDGET_REFRESH_MAX_RUN=180
BTT_WIDGET_QUOTA_RESET_CYCLE_MINUTES=300
BTT_WIDGET_QUOTA_SECONDARY_RESET_CYCLE_MINUTES=10080

REPO_DIR="${BTT_REPO_DIR:-$HOME/Documents/BTT}"
LOG_DIR="${BTT_LOG_DIR:-$REPO_DIR/logs}"
LOG="$LOG_DIR/btt-codexbar.log"

compute_value() {
    cd "$HOME" || {
        printf 'HOME ERR'
        return 0
    }

    local codexbar="/usr/local/bin/codexbar"
    local jq
    jq="$(command -v jq)"

    mkdir -p "$LOG_DIR"

    if [[ ! -x "$codexbar" ]]; then
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
            --provider claude \
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
                select(.provider == "claude" and .usage != null)
                | .usage.primary.resetsAt
                | if . == null then empty else fromdateiso8601 end
            )
            | first // empty
        ' 2>>"$LOG"
    )"
    local reset_status=$?
    if (( reset_status == 0 )); then
        btt_quota_reset_put "$BTT_WIDGET_NAME" "$reset_at"
    fi

    local secondary_reset_at
    secondary_reset_at="$(
        printf '%s' "$json" | "$jq" -r '
            (if type == "array" then . else [.] end)
            | map(
                select(.provider == "claude" and .usage != null)
                | .usage.secondary.resetsAt
                | if . == null then empty else fromdateiso8601 end
            )
            | first // empty
        ' 2>>"$LOG"
    )"
    local secondary_reset_status=$?
    if (( secondary_reset_status == 0 )); then
        btt_quota_reset_put "${BTT_WIDGET_NAME}-secondary" "$secondary_reset_at"
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
                if .provider == "claude" and .usage != null then
                                        .usage.primary as $primary
                                        | .usage.secondary as $secondary
                                        | if ($secondary.usedPercent // 0) >= 100 then
                                                used($secondary.usedPercent)
                                                + " " + until_reset($secondary.resetsAt)
                                                + "\n  " + used($primary.usedPercent)
                                                + " " + until_reset($primary.resetsAt)
                                            else
                                                used($primary.usedPercent)
                                                + " " + until_reset($primary.resetsAt)
                                                + "\n" + used($secondary.usedPercent)
                                                + " " + until_reset($secondary.resetsAt)
                                            end
                else
                    empty
                end
            )
            | join("   ")
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

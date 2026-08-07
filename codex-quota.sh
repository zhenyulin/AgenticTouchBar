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

source "$HOME/Documents/BTT/lib/btt-widget.sh"

STARTED="$(btt_now)"

BTT_WIDGET_NAME="codex-quota"

VALUE_MAX_AGE="${CODEX_QUOTA_MAX_AGE:-300}"
BTT_WIDGET_REFRESH_MAX_RUN=180

LOG="$HOME/Library/Logs/btt-codexbar.log"

compute_value() {
    cd "$HOME" || {
        printf 'HOME ERR'
        return 0
    }

    local codexbar jq
    codexbar="$(command -v codexbar)"
    jq="$(command -v jq)"

    mkdir -p "$HOME/Library/Logs"

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
        printf 'JSON ERR'
        return 0
    fi

    printf '%s' "$text"
}

if (( REFRESH_MODE )); then
    REFRESHED="$(compute_value)"
    btt_cache_put "$BTT_WIDGET_NAME" "$REFRESHED"

    # A small vocabulary in the outcome column, with the value itself kept
    # alongside: these widgets report failures as text ("JSON ERR",
    # "NO CODEXBAR"), and using that text as the outcome would give every
    # distinct reading its own bucket in --report's tally.
    case "$REFRESHED" in
        "")            REFRESH_OUTCOME=empty ;;
        *ERR*|NO\ *)   REFRESH_OUTCOME=error ;;
        *)             REFRESH_OUTCOME=ok ;;
    esac
    btt_trace refresh "$STARTED" "$REFRESH_OUTCOME" "value=$REFRESHED"
    exit 0
fi

VALUE="$(btt_cache_get "$BTT_WIDGET_NAME" "$VALUE_MAX_AGE")"
FRESH=$?

# A tap forces the refresh even when the cached value is still fresh --
# without this, tapping inside the freshness window only repaints.
FORCE=0
if btt_force_pending; then
    FORCE=1
fi

if (( FRESH != 0 || FORCE )); then
    btt_refresh_detached \
        "$BTT_WIDGET_NAME" "$BTT_WIDGET_REFRESH_MAX_RUN" \
        "$SELF" --refresh "$BTT_WIDGET_UUID"
fi

OUTCOME=cached
(( FRESH != 0 )) && OUTCOME=stale
(( FORCE )) && OUTCOME=forced
[[ -n "$VALUE" ]] || OUTCOME=empty
btt_trace widget "$STARTED" "$OUTCOME"
btt_publish "${VALUE:-…}"

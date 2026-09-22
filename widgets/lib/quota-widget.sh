#!/usr/bin/env zsh

#
# Shared codexbar quota widget support.
#
# The three quota widgets ask codexbar for one provider's usage and draw one
# or two of its rolling windows. That is the only thing they differ in: the
# provider name, which window each row reads, and how the rows are ordered.
# Everything else -- the refresh split, resolving codexbar and jq, the error
# tokens, persisting the exact reset time so btt_quota_color can shade the
# label by progress toward it -- was copied three times, and is here instead.
#
# A quota widget states its identity and its windows, then calls
# quota_widget_main:
#
#   BTT_WIDGET_NAME="codex-quota"
#   QUOTA_PROVIDER="codex"
#   QUOTA_WINDOW='.usage.primary // .usage.secondary // .usage.tertiary'
#   quota_widget_main "$REFRESH_MODE" "$SELF" "$VALUE_MAX_AGE"
#
# Optional:
#   QUOTA_SECONDARY_WINDOW   a second window, bound as $secondary in QUOTA_ROWS
#                            and persisted under "<name>-secondary" for
#                            BTT_WIDGET_QUOTA_SECONDARY_RESET_CYCLE_MINUTES.
#   QUOTA_ROWS               the jq expression producing the widget text, with
#                            $window (and $secondary) bound. The default is one
#                            window's used percentage over its time to reset.
#   QUOTA_FETCH              the function that prints the usage JSON, for a
#                            provider whose truth codexbar cannot see. It
#                            prints one codexbar-shaped record (records whose
#                            .provider is QUOTA_PROVIDER are kept) or an error
#                            token and a non-zero status. The default asks
#                            codexbar.
#
# Why the refresh is detached at all: codexbar's Claude lookup takes the best
# part of a minute, and BTT runs every shell widget through one XPC service,
# so doing it inline freezes every other widget -- and every tap-refresh --
# for that long. See widgets/lib/btt-widget.sh.
#

QUOTA_PROVIDER="${QUOTA_PROVIDER:-}"
QUOTA_WINDOW="${QUOTA_WINDOW:-.usage.primary}"
QUOTA_SECONDARY_WINDOW="${QUOTA_SECONDARY_WINDOW:-null}"
QUOTA_FETCH="${QUOTA_FETCH:-quota__codexbar_usage}"

# The default row layout: "12%" over "3h".
QUOTA_ROWS="${QUOTA_ROWS:-used(\$window.usedPercent) + \"\\n\" + until_reset(\$window.resetsAt)}"

QUOTA_LOG_DIR="${BTT_LOG_DIR:-${BTT_REPO_DIR:-$HOME/Documents/BTT}/logs}"
QUOTA_LOG="$QUOTA_LOG_DIR/btt-codexbar.log"

# jq definitions the render expressions are evaluated against. `records`
# normalises codexbar's output, which is a bare object for one provider and an
# array for several.
QUOTA_JQ_PRELUDE='
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

def records:
    if type == "array" then . else [.] end;
'

# jq renders every widget's rows, so the shell resolves it once per refresh.
QUOTA_JQ=""

# codexbar belongs to the default fetch alone: a widget that fetches its own
# usage must not report NO CODEXBAR for a dependency it never calls.
quota__resolve_jq() {
    QUOTA_JQ="$(command -v jq)"

    mkdir -p "$QUOTA_LOG_DIR" 2>/dev/null

    [[ -n "$QUOTA_JQ" && -x "$QUOTA_JQ" ]] || { printf 'NO JQ'; return 1; }
}

# The default fetch: ask codexbar for the provider's usage.
quota__codexbar_usage() {
    local codexbar
    codexbar="$(command -v codexbar)"

    [[ -n "$codexbar" && -x "$codexbar" ]] || {
        printf 'NO CODEXBAR'
        return 1
    }

    local json
    json="$(
        "$codexbar" usage \
            --provider "$QUOTA_PROVIDER" \
            --source auto \
            --format json \
            2>>"$QUOTA_LOG"
    )"

    if [[ -z "$json" ]]; then
        printf 'EMPTY JSON'
        return 1
    fi

    printf '%s' "$json"
}

# One jq run over the fetched JSON, with the prelude in scope.
quota__jq() {
    local json="$1" expression="$2"
    printf '%s' "$json" | "$QUOTA_JQ" -r --arg provider "$QUOTA_PROVIDER" \
        "$QUOTA_JQ_PRELUDE $expression" 2>>"$QUOTA_LOG"
}

# The reset instant of one window, as epoch seconds, stored beside the value.
# btt_quota_color prefers it over parsing the rendered "3h" label back.
quota__persist_reset() {
    local json="$1" window="$2" name="$3" reset_at

    reset_at="$(quota__jq "$json" "
        records
        | map(
            select(.provider == \$provider and .usage != null)
            | ($window)
            | select(. != null)
            | .resetsAt
            | if . == null then empty else fromdateiso8601 end
        )
        | first // empty
    ")"
    # Not `status`: zsh reserves that name as a read-only alias for \$?.
    local jq_status=$?

    (( jq_status == 0 )) && btt_quota_reset_put "$name" "$reset_at"
}

quota_compute_value() {
    cd "$HOME" || {
        printf 'HOME ERR'
        return 0
    }

    quota__resolve_jq || return 0

    # A fetch reports its own broken dependency in the row: it prints the token
    # on stdout and a non-zero status, so a failed fetch is never mistaken for
    # a usage record.
    local json
    json="$("$QUOTA_FETCH")"
    local fetch_status=$?

    if (( fetch_status != 0 )); then
        printf '%s' "$json"
        return 0
    fi

    quota__persist_reset "$json" "$QUOTA_WINDOW" "$BTT_WIDGET_NAME"
    if [[ "$QUOTA_SECONDARY_WINDOW" != "null" ]]; then
        quota__persist_reset "$json" "$QUOTA_SECONDARY_WINDOW" \
            "${BTT_WIDGET_NAME}-secondary"
    fi

    local text
    text="$(quota__jq "$json" "
        records
        | map(
            select(.provider == \$provider and .usage != null)
            | ($QUOTA_WINDOW) as \$window
            | ($QUOTA_SECONDARY_WINDOW) as \$secondary
            | select(\$window != null)
            | ($QUOTA_ROWS)
        )
        | first // empty
    ")"
    local jq_status=$?

    if (( jq_status != 0 )) || [[ -z "$text" ]]; then
        printf 'ERR'
        return 0
    fi

    printf '%s' "$text"
}

quota_widget_main() {
    btt_cached_widget_main "$1" "$2" "$3" quota_compute_value
}

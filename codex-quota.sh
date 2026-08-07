#!/bin/zsh

export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
export HOME="${HOME:-/Users/zhenyulin}"

# Optional first argument: BTT widget UUID.
if [[ -n "${1:-}" ]]; then
    BTT_WIDGET_UUID="$1"
    shift
else
    BTT_WIDGET_UUID="${BTT_WIDGET_UUID:-}"
fi

source "$HOME/Documents/BTT/lib/btt-widget.sh"

if btt_refresh_gate "$0" "$@"; then
    exit 0
fi

cd "$HOME" || {
    echo "HOME ERR"
    exit 0
}

CODEXBAR="$(command -v codexbar)"
JQ="$(command -v jq)"
LOG="$HOME/Library/Logs/btt-codexbar.log"

mkdir -p "$HOME/Library/Logs"

if [[ -z "$CODEXBAR" || ! -x "$CODEXBAR" ]]; then
    echo "NO CODEXBAR"
    exit 0
fi

JSON="$(
    "$CODEXBAR" \
        usage \
        --provider codex \
        --source auto \
        --format json \
        2>>"$LOG"
)"

if [[ -z "$JSON" ]]; then
    echo "EMPTY JSON"
    exit 0
fi

if [[ -z "$JQ" || ! -x "$JQ" ]]; then
    echo "NO JQ"
    exit 0
fi

TEXT="$(
    printf '%s' "$JSON" | "$JQ" -r '
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
STATUS=$?

if (( STATUS != 0 )) || [[ -z "$TEXT" ]]; then
    echo "JSON ERR"
    exit 0
fi

btt_publish "$TEXT"
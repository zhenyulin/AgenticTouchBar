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

CODEXBAR="/usr/local/bin/codexbar"
JQ="$(command -v jq)"
LOG="$HOME/Library/Logs/btt-codexbar.log"
CODEX_LOGO="◉"
CLAUDE_LOGO="✳"

mkdir -p "$HOME/Library/Logs"

if [[ ! -x "$CODEXBAR" ]]; then
    echo "NO CODEXBAR"
    exit 0
fi

if [[ -z "$JQ" || ! -x "$JQ" ]]; then
    echo "NO JQ"
    exit 0
fi

# Let CodexBar honor its app-managed provider credentials and source settings.
JSON="$(
    "$CODEXBAR" \
        --provider both \
        --source auto \
        --format json \
        2>"$LOG"
)"

if [[ -z "$JSON" ]]; then
    echo "EMPTY JSON"
    exit 0
fi

TEXT="$(
    printf '%s' "$JSON" | "$JQ" -r \
        --arg codex_logo "$CODEX_LOGO" \
        --arg claude_logo "$CLAUDE_LOGO" '
        def used($value):
            if $value == null then "—"
            else ($value | round | tostring) + "%"
            end;

        (if type == "array" then . else [.] end)
        | map(
            if .provider == "codex" and .usage != null then
                $codex_logo + " " +
                (
                    if .usage.primary.usedPercent == null then
                        "W " + used(.usage.secondary.usedPercent)
                    else
                        "5h " + used(.usage.primary.usedPercent)
                        + " · W " + used(.usage.secondary.usedPercent)
                    end
                )
            elif .provider == "claude" and .usage != null then
                $claude_logo + " 5h " + used(.usage.primary.usedPercent)
                + " · W " + used(.usage.secondary.usedPercent)
            else
                empty
            end
        )
        | join("   ")
    ' 2>>"$LOG"
)"
STATUS=$?

if (( STATUS != 0 )) || [[ -z "$TEXT" ]]; then
    echo "JSON ERR"
    exit 0
fi

btt_publish "$TEXT"
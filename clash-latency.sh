#!/usr/bin/env zsh
#
# BetterTouchTool Touch Bar script widget for the currently selected
# Clash Verge / Mihomo proxy node.
#

set -u
set -o pipefail

# BTT does not necessarily inherit your interactive shell PATH.
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# Optional first argument: BTT widget UUID. Without it the script simply
# behaves like an ordinary terminal command.
if [[ -n "${1:-}" ]]; then
    BTT_WIDGET_UUID="$1"
    shift
else
    BTT_WIDGET_UUID="${BTT_WIDGET_UUID:-}"
fi

source "$HOME/Documents/BTT/lib/btt-widget.sh"

# Optional local PNG icon. Set before the dim gate so that a dimmed frame
# keeps the icon too.
ICON_PATH="${CLASH_ICON:-}"
BTT_WIDGET_ICON="$ICON_PATH"

# A tap asked for this widget to grey out while it refreshes: reprint the
# previous value dimmed and let the refresh that follows do the real work.
if btt_dim_gate; then
    exit 0
fi

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API="${CLASH_API:-http://127.0.0.1:9097}"
SECRET="${CLASH_SECRET:-}"
SOCKET="${CLASH_SOCKET:-/tmp/verge/verge-mihomo.sock}"
ROOT_GROUP="${CLASH_GROUP:-PROXY}"

TEST_URL="${CLASH_TEST_URL:-https://cp.cloudflare.com/generate_204}"
TIMEOUT_MS="${CLASH_TIMEOUT_MS:-3000}"

# latency | node | full
LABEL_MODE="${CLASH_LABEL_MODE:-full}"

# ---------------------------------------------------------------------------
# BetterTouchTool appearance
# ---------------------------------------------------------------------------

# The idle color has to be stated explicitly: a tap greys the widget out
# while the manual refresh runs, and only the JSON emitted here can restore
# it.
FONT_COLOR="${CLASH_FONT_COLOR:-$BTT_WIDGET_COLOR}"

# No font_size is emitted: leaving it out lets the widget render at the size
# configured for it in BetterTouchTool.

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

emit_widget() {
    local text="$1"

    # Remembered so a tap can grey out this exact value.
    btt_remember "$text"

    if [[ -n "$ICON_PATH" && -f "$ICON_PATH" ]]; then
        jq -cn \
            --arg text "$text" \
            --arg color "$FONT_COLOR" \
            --arg icon "$ICON_PATH" \
            '{
                text: $text,
                font_color: $color,
                icon_path: $icon
            }'
    else
        jq -cn \
            --arg text "$text" \
            --arg color "$FONT_COLOR" \
            '{
                text: $text,
                font_color: $color
            }'
    fi
}

fail() {
    emit_widget "$1"
    exit 0
}

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

command -v curl >/dev/null 2>&1 || {
    printf '%s\n' "No curl"
    exit 0
}

command -v jq >/dev/null 2>&1 || {
    printf '%s\n' "No jq"
    exit 0
}

[[ "$TIMEOUT_MS" =~ ^[0-9]+$ ]] || TIMEOUT_MS=3000

uri_encode() {
    jq -rn --arg value "$1" '$value | @uri'
}

# ---------------------------------------------------------------------------
# Clash connection
# ---------------------------------------------------------------------------

CURL_ARGS=(--noproxy '*')

if [[ -S "$SOCKET" ]]; then
    CURL_ARGS+=(--unix-socket "$SOCKET")
fi

# Seeded with a harmless header on purpose: under `set -u`, bash 3.2
# (which is what /bin/bash and a PATH-less `env bash` resolve to) aborts on
# "${EMPTY_ARRAY[@]}" with an unbound-variable error.
AUTH_HEADERS=(-H "Accept: application/json")

if [[ -n "$SECRET" ]]; then
    AUTH_HEADERS+=(-H "Authorization: Bearer $SECRET")
fi

fetch_proxy() {
    local proxy="$1"

    curl -fsS \
        --connect-timeout 1 \
        --max-time 2 \
        "${CURL_ARGS[@]}" \
        "${AUTH_HEADERS[@]}" \
        "$API/proxies/$(uri_encode "$proxy")"
}

# ---------------------------------------------------------------------------
# Resolve currently selected leaf node
# ---------------------------------------------------------------------------

CURRENT="$ROOT_GROUP"
NODE=""

for _ in {1..8}; do
    PROXY_JSON="$(fetch_proxy "$CURRENT")" || fail "Controller"

    NEXT="$(
        jq -r '.now // empty' <<< "$PROXY_JSON" 2>/dev/null
    )" || fail "Bad JSON"

    if [[ -z "$NEXT" || "$NEXT" == "$CURRENT" ]]; then
        NODE="$CURRENT"
        break
    fi

    CURRENT="$NEXT"
done

[[ -n "$NODE" ]] || fail "Group loop"

# ---------------------------------------------------------------------------
# Latency test
# ---------------------------------------------------------------------------

DELAY_JSON="$(
    curl -fsS \
        --connect-timeout 1 \
        --max-time 5 \
        "${CURL_ARGS[@]}" \
        "${AUTH_HEADERS[@]}" \
        "$API/proxies/$(uri_encode "$NODE")/delay?url=$(uri_encode "$TEST_URL")&timeout=$TIMEOUT_MS"
)" || DELAY_JSON=""

DELAY="$(
    jq -r '.delay // 0' <<< "$DELAY_JSON" 2>/dev/null ||
        printf '0'
)"

# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

if [[ "$DELAY" =~ ^[0-9]+$ ]] && (( DELAY > 0 )); then

    case "$LABEL_MODE" in
        node)
            TEXT="$NODE"
            ;;
        full)
            TEXT="$NODE · ${DELAY}ms"
            ;;
        *)
            TEXT="${DELAY}ms"
            ;;
    esac

    emit_widget "$TEXT"

else
    emit_widget "Timeout"
fi
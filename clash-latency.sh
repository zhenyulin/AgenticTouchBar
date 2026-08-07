#!/usr/bin/env zsh
#
# BetterTouchTool Touch Bar script widget for the currently selected
# Clash Verge / Mihomo proxy node.
#

set -u
set -o pipefail

# BTT does not necessarily inherit your interactive shell PATH.
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# Refresh mode runs the controller queries, detached from the widget path
# below. Resolving the selected node walks up to eight groups and then runs a
# latency probe, so a controller that has gone away costs about twenty
# seconds -- and BTT runs every shell widget through one XPC service, which
# would freeze all of them for that long.
REFRESH_MODE=0
if [[ "${1:-}" == "--refresh" ]]; then
    REFRESH_MODE=1
    shift
fi

# Optional first argument: BTT widget UUID. Without it the script simply
# behaves like an ordinary terminal command.
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

# Optional local PNG icon. Set before the dim gate so that a dimmed frame
# keeps the icon too.
ICON_PATH="${CLASH_ICON:-}"
BTT_WIDGET_ICON="$ICON_PATH"

BTT_WIDGET_NAME="clash-latency"

# A latency reading goes out of date quickly, so this is much shorter than
# the quota widgets'. It still costs the widget nothing: the probe runs in
# the background and asks BTT to redraw when it has an answer.
VALUE_MAX_AGE="${CLASH_MAX_AGE:-30}"
BTT_WIDGET_REFRESH_MAX_RUN=60

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

# Left empty to follow the widget's refresh state -- normal when idle, dim
# while a refresh is in flight. Setting CLASH_FONT_COLOR pins one color and
# opts out of the grey-out.
FONT_COLOR="${CLASH_FONT_COLOR:-}"

# No font_size is emitted: leaving it out lets the widget render at the size
# configured for it in BetterTouchTool.

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

emit_widget() {
    local text="$1"

    # Resolved per emit, not at startup: the refresh this run may have just
    # started is what decides the color.
    local color="${FONT_COLOR:-$(btt_current_color)}"

    if [[ -n "$ICON_PATH" && -f "$ICON_PATH" ]]; then
        jq -cn \
            --arg text "$text" \
            --arg color "$color" \
            --arg icon "$ICON_PATH" \
            '{
                text: $text,
                font_color: $color,
                icon_path: $icon
            }'
    else
        jq -cn \
            --arg text "$text" \
            --arg color "$color" \
            '{
                text: $text,
                font_color: $color
            }'
    fi
}

# ---------------------------------------------------------------------------
# The slow half: everything that talks to the Clash controller
# ---------------------------------------------------------------------------

uri_encode() {
    jq -rn --arg value "$1" '$value | @uri'
}

compute_value() {
    # compute_value is only ever run inside a command substitution, so these
    # early exits leave that subshell, not the script.
    fail() {
        printf '%s' "$1"
        exit 0
    }

    command -v curl >/dev/null 2>&1 || fail "No curl"
    command -v jq >/dev/null 2>&1 || fail "No jq"

    [[ "$TIMEOUT_MS" =~ ^[0-9]+$ ]] || TIMEOUT_MS=3000

    local curl_args=(--noproxy '*')

    if [[ -S "$SOCKET" ]]; then
        curl_args+=(--unix-socket "$SOCKET")
    fi

    # Seeded with a harmless header on purpose: under `set -u`, bash 3.2
    # (which is what /bin/bash and a PATH-less `env bash` resolve to) aborts
    # on "${EMPTY_ARRAY[@]}" with an unbound-variable error.
    local auth_headers=(-H "Accept: application/json")

    if [[ -n "$SECRET" ]]; then
        auth_headers+=(-H "Authorization: Bearer $SECRET")
    fi

    fetch_proxy() {
        curl -fsS \
            --connect-timeout 1 \
            --max-time 2 \
            "${curl_args[@]}" \
            "${auth_headers[@]}" \
            "$API/proxies/$(uri_encode "$1")"
    }

    # Resolve the currently selected leaf node.
    local current="$ROOT_GROUP"
    local node=""
    local proxy_json next

    for _ in {1..8}; do
        proxy_json="$(fetch_proxy "$current")" || fail "Controller"

        next="$(
            jq -r '.now // empty' <<< "$proxy_json" 2>/dev/null
        )" || fail "Bad JSON"

        if [[ -z "$next" || "$next" == "$current" ]]; then
            node="$current"
            break
        fi

        current="$next"
    done

    [[ -n "$node" ]] || fail "Group loop"

    # Latency test.
    local delay_json delay
    delay_json="$(
        curl -fsS \
            --connect-timeout 1 \
            --max-time 5 \
            "${curl_args[@]}" \
            "${auth_headers[@]}" \
            "$API/proxies/$(uri_encode "$node")/delay?url=$(uri_encode "$TEST_URL")&timeout=$TIMEOUT_MS"
    )" || delay_json=""

    delay="$(
        jq -r '.delay // 0' <<< "$delay_json" 2>/dev/null ||
            printf '0'
    )"

    if [[ "$delay" =~ ^[0-9]+$ ]] && (( delay > 0 )); then
        case "$LABEL_MODE" in
            node)
                printf '%s' "$node"
                ;;
            full)
                printf '%s · %sms' "$node" "$delay"
                ;;
            *)
                printf '%sms' "$delay"
                ;;
        esac
    else
        printf 'Timeout'
    fi
}

# ---------------------------------------------------------------------------
# Refresh mode
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Widget path: read the last reading and return immediately
# ---------------------------------------------------------------------------

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
emit_widget "${VALUE:-…}"

#!/bin/zsh
#
# BetterTouchTool Touch Bar widget that measures latency to the
# currently selected Clash Verge / Mihomo proxy node.
#

set -u
set -o pipefail

# BTT does not necessarily inherit your interactive shell's PATH.
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

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

# Clash Verge / Mihomo configuration.
API="${CLASH_API:-http://127.0.0.1:9097}"
SECRET="${CLASH_SECRET:-}"
SOCKET="${CLASH_SOCKET:-/tmp/verge/verge-mihomo.sock}"
ROOT_GROUP="${CLASH_GROUP:-PROXY}"

# Latency test configuration.
TEST_URL="${CLASH_TEST_URL:-https://cp.cloudflare.com/generate_204}"
TIMEOUT_MS="${CLASH_TIMEOUT_MS:-3000}"

# Display:
#   latency -> 123ms
#   node    -> Hong Kong 01
#   full    -> Hong Kong 01 · 123ms
LABEL_MODE="${CLASH_LABEL_MODE:-full}"

emit_widget() {
  btt_publish "$1"
}

fail() {
  emit_widget "$1"
  exit 0
}

command -v curl >/dev/null 2>&1 || fail "No curl"
command -v jq >/dev/null 2>&1 || fail "No jq"

[[ "$TIMEOUT_MS" =~ ^[0-9]+$ ]] || TIMEOUT_MS=3000

uri_encode() {
  jq -rn --arg value "$1" '$value | @uri'
}

CURL_ARGS=(--noproxy "*")

if [[ -S "$SOCKET" ]]; then
  CURL_ARGS+=(--unix-socket "$SOCKET")
fi

AUTH_HEADERS=()

if [[ -n "$SECRET" ]]; then
  AUTH_HEADERS=(-H "Authorization: Bearer $SECRET")
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

# Recursively follow selector groups:
#
# PROXY -> Auto -> Hong Kong -> actual node
#
# A leaf proxy has no `.now` field.
CURRENT="$ROOT_GROUP"
NODE=""

for _ in {1..8}; do
  PROXY_JSON=$(fetch_proxy "$CURRENT") || fail "Controller"

  NEXT=$(
    jq -r '.now // empty' <<<"$PROXY_JSON" 2>/dev/null
  ) || fail "Bad JSON"

  if [[ -z "$NEXT" || "$NEXT" == "$CURRENT" ]]; then
    NODE="$CURRENT"
    break
  fi

  CURRENT="$NEXT"
done

[[ -n "$NODE" ]] || fail "Group loop"

DELAY_JSON=$(
  curl -fsS \
    --connect-timeout 1 \
    --max-time 5 \
    "${CURL_ARGS[@]}" \
    "${AUTH_HEADERS[@]}" \
    "$API/proxies/$(uri_encode "$NODE")/delay?url=$(uri_encode "$TEST_URL")&timeout=$TIMEOUT_MS"
) || DELAY_JSON=""

DELAY=$(
  jq -r '.delay // 0' <<<"$DELAY_JSON" 2>/dev/null ||
    printf "0"
)

if [[ "$DELAY" =~ ^[0-9]+$ ]] && ((DELAY > 0)); then
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
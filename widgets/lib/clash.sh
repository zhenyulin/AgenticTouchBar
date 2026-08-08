#!/usr/bin/env zsh

# Shared Clash/Mihomo controller helpers for the Clash widgets.

API="${CLASH_API:-http://127.0.0.1:9097}"
SECRET="${CLASH_SECRET:-}"
SOCKET="${CLASH_SOCKET:-/tmp/verge/verge-mihomo.sock}"
ROOT_GROUP="${CLASH_GROUP:-PROXY}"

clash_uri_encode() {
    jq -rn --arg value "$1" '$value | @uri'
}

clash_setup_curl() {
    CLASH_CURL_ARGS=(--noproxy '*')
    if [[ -S "$SOCKET" ]]; then
        CLASH_CURL_ARGS+=(--unix-socket "$SOCKET")
    fi

    CLASH_AUTH_HEADERS=(-H "Accept: application/json")
    if [[ -n "$SECRET" ]]; then
        CLASH_AUTH_HEADERS+=(-H "Authorization: Bearer $SECRET")
    fi
}

clash_fetch_proxy() {
    curl -fsS \
        --connect-timeout 1 \
        --max-time 2 \
        "${CLASH_CURL_ARGS[@]}" \
        "${CLASH_AUTH_HEADERS[@]}" \
        "$API/proxies/$(clash_uri_encode "$1")"
}

clash_fetch_proxies() {
    curl -fsS \
        --connect-timeout 1 \
        --max-time 2 \
        "${CLASH_CURL_ARGS[@]}" \
        "${CLASH_AUTH_HEADERS[@]}" \
        "$API/proxies"
}

clash_resolve_node() {
    local current="$ROOT_GROUP"
    local proxies_json proxy_json next next_json

    clash_setup_curl
    proxies_json="$(clash_fetch_proxies)" || return 1

    if ! jq -e --arg name "$current" '.proxies | has($name)' <<< "$proxies_json" >/dev/null 2>&1; then
        current="$(jq -r '
            .proxies
            | to_entries[]
            | select((.value.all // []) | length > 0)
            | .key
        ' <<< "$proxies_json" | head -n 1)"
        [[ -n "$current" ]] || return 1
    fi

    for _ in {1..8}; do
        proxy_json="$(jq -c --arg name "$current" '.proxies[$name] // empty' <<< "$proxies_json")"
        [[ -n "$proxy_json" ]] || return 1
        next="$(jq -r '.now // empty' <<< "$proxy_json" 2>/dev/null)" || return 1

        if [[ -z "$next" || "$next" == "$current" ]]; then
            printf '%s' "$current"
            return 0
        fi

        next_json="$(jq -c --arg name "$next" '.proxies[$name] // empty' <<< "$proxies_json")"
        if [[ -n "$next_json" ]]; then
            current="$next"
            continue
        fi

        current="$(jq -r --argjson proxy "$proxy_json" '
            .proxies as $all
            | $proxy.all[]?
            | select($all[.] != null)
        ' <<< "$proxies_json" 2>/dev/null | head -n 1)"
        [[ -n "$current" ]] || return 1
    done

    return 1
}

clash_region_flag() {
    local node="$1" code="${CLASH_REGION:-}" token existing_flag

    # Preserve a flag already included in the proxy name.
    existing_flag="$(jq -rn --arg node "$node" '
        $node | explode as $cp
        | if ($cp | length) >= 2
          and $cp[0] >= 127462 and $cp[0] <= 127487
          and $cp[1] >= 127462 and $cp[1] <= 127487
          then ($cp[0:2] | implode) else empty end
    ')"
    [[ -n "$existing_flag" ]] && { printf '%s' "$existing_flag"; return; }

    # Common non-Latin and written-out names that do not contain a country
    # code for the token-based fallback below.
    if [[ "$node" == *日本* || "$node" == *Japan* || "$node" == *japan* ||
        "$code" == 日本 || "$code" == Japan || "$code" == japan ]]; then
        code=JP
    fi

    if [[ -n "$code" && ! "$code" =~ ^[A-Za-z]{2}$ ]]; then
        printf '%s' "$code"
        return
    fi
    if [[ -z "$code" ]]; then
        token="$(printf '%s' "$node" | grep -Eo '(^|[^A-Za-z])[A-Za-z]{2}([^A-Za-z]|$)' | head -n 1 | tr -cd 'A-Za-z')"
        code="$token"
    fi
    if [[ "$code" =~ ^[A-Za-z]{2}$ ]]; then
        jq -rn --arg code "$code" '$code | ascii_upcase | explode | map(. + 127397) | implode'
    else
        printf '🌐'
    fi
}

clash_proxy_label() {
    jq -rn --arg node "$1" '
        $node
        | explode as $codepoints
        | if ($codepoints | length) >= 2
          and $codepoints[0] >= 127462 and $codepoints[0] <= 127487
          and $codepoints[1] >= 127462 and $codepoints[1] <= 127487
          then ($codepoints[2:] | implode | sub("^[[:space:]]+"; ""))
          else $node
          end
    '
}

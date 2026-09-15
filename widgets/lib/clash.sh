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

# The selected leaf node, following each group's `now` down to a proxy that
# selects nothing further.
#
# One jq pass rather than up to four per hop over eight hops: the controller's
# /proxies document is ~180 KB here, and the loop this replaces re-parsed the
# whole of it on every jq call it made.
clash_resolve_node() {
    local proxies_json

    clash_setup_curl
    proxies_json="$(clash_fetch_proxies)" || return 1

    jq -er --arg root "$ROOT_GROUP" '
        .proxies as $all

        # The configured group, or the first group that selects anything --
        # a controller may not carry the name this repo defaults to.
        | (if $all | has($root) then $root
           else [$all | to_entries[]
                 | select((.value.all // []) | length > 0)
                 | .key] | first
           end) as $start
        | if $start == null then empty else

        reduce range(0; 8) as $_ ({current: $start, settled: false, failed: false};
            if .settled or .failed then .
            else
                .current as $name
                | $all[$name] as $proxy
                | if $proxy == null then .failed = true
                  else
                      ($proxy.now // "") as $next
                      # Selecting nothing, or itself, makes this the leaf.
                      | if $next == "" or $next == $name then .settled = true
                        elif $all | has($next) then .current = $next
                        else
                            # `now` names something the document does not
                            # describe: fall back to the first member it does.
                            ([$proxy.all[]? | select($all[.] != null)] | first) as $member
                            | if $member == null then .failed = true
                              else .current = $member
                              end
                        end
                  end
            end)
        | if .settled then .current else empty end
        end
    ' <<< "$proxies_json" 2>/dev/null
}

clash_region_code_from_name() {
    local name="$1" lower condensed region
    lower="${name:l}"
    condensed="${lower//[[:space:]]/}"

    # Asia focus, written out more often than appended as a code. Checked
    # before the token scan, so e.g. "AZ 台湾 01" still resolves to TW.
    if [[ "$lower" == *台湾* || "$lower" == *台湾* || "$condensed" == *taiwan* ]]; then
        region=TW
    elif [[ "$lower" == *香港* || "$condensed" == *hongkong* ]]; then
        region=HK
    elif [[ "$lower" == *新加坡* || "$condensed" == *singapore* ]]; then
        region=SG
    elif [[ "$lower" == *日本* || "$condensed" == *japan* ]]; then
        region=JP
    elif [[ "$lower" == *韩* || "$lower" == *韓* || "$condensed" == *korea* ]] \
        && [[ "$lower" != *north*korea* ]]; then
        region=KR
    else
        # First two-letter token bounded by non-letters — TW, JP, US.
        region="$(printf '%s' "$name" | grep -Eo '(^|[^A-Za-z])[A-Za-z]{2}([^A-Za-z]|$)' | head -n 1 | tr -cd 'A-Za-z')"
    fi

    printf '%s' "$region"
}

clash_region_flag() {
    local node="$1" code="${CLASH_REGION:-}" existing_flag

    # CLASH_REGION is the escape hatch: a written-out name or a two-letter
    # code is parsed like a node name, anything else is a custom glyph as-is.
    if [[ -n "$code" ]]; then
        code="$(clash_region_code_from_name "$code")"
        [[ -n "$code" ]] || { printf '%s' "${CLASH_REGION:-}"; return; }
    else
        # Name parsing is the primary source over a flag the provider embeds:
        # some providers pair Taiwan/TW with the PRC flag, which would be
        # incoherent across providers.
        code="$(clash_region_code_from_name "$node")"
    fi

    if [[ -n "$code" ]]; then
        jq -rn --arg code "$code" '$code | ascii_upcase | explode | map(. + 127397) | implode'
        return
    fi

    # Preserve a flag already included in the proxy name.
    existing_flag="$(jq -rn --arg node "$node" '
        $node | explode as $cp
        | if ($cp | length) >= 2
          and $cp[0] >= 127462 and $cp[0] <= 127487
          and $cp[1] >= 127462 and $cp[1] <= 127487
          then ($cp[0:2] | implode) else empty end
    ')"
    if [[ -n "$existing_flag" ]]; then
        printf '%s' "$existing_flag"
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
        # Provider metadata after a pipe -- "TW 08 | 家宽-直连× 0.5" -- is
        # noise beside a widget that already names the node: keep what is
        # left of the first pipe, drop the rest.
        | sub("[[:space:]]*[|].*$"; "")
        | sub("[[:space:]]+$"; "")
    '
}

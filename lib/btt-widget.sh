#!/usr/bin/env bash

#
# Shared BetterTouchTool Script Widget support.
#
# Public functions:
#
#   btt_dim_gate
#       Call near the top of a widget script:
#
#           if btt_dim_gate; then
#               exit 0
#           fi
#
#   btt_publish "$result"
#       Record and emit the final widget result.
#       Call instead of echo/printf.
#
#   btt_remember "$result"
#       Record only, for a widget that emits its own JSON.
#
#
# BTT mode:
#   BTT_WIDGET_UUID must be set.
#
# Terminal mode:
#   BTT_WIDGET_UUID may be unset.
#   The script behaves like an ordinary shell script.
#
#
# Why the grey-out works this way
# ------------------------------------------------------------------
# A widget's font color can only be set by the JSON the widget script
# itself prints. BTT's update_touch_bar_widget AppleScript command takes
# text, icon_path, sf_symbol_* , icon_data and background_color -- there is
# no font_color and no font_size parameter, so an external process cannot
# grey a widget out directly.
#
# So actions/tap-refresh.sh only drops a flag file and asks BTT to re-run
# the widget. That run hits btt_dim_gate, which reprints the previous value
# in grey and exits immediately; the refresh that follows repaints it in the
# normal color. The flag is a file rather than a BTT variable so that the
# common case -- an ordinary scheduled tick -- costs a stat() instead of an
# osascript round trip.
#

BTT_WIDGET_CACHE_DIR="${BTT_WIDGET_CACHE_DIR:-$HOME/Library/Caches/btt-widgets}"

# The normal, undimmed label color.
BTT_WIDGET_COLOR="${BTT_WIDGET_COLOR:-255,255,255,255}"

# The color used while a manual refresh is in flight.
BTT_WIDGET_DIM_COLOR="${BTT_WIDGET_DIM_COLOR:-130,130,130,255}"

# A dim flag older than this belongs to a refresh that never arrived.
BTT_WIDGET_DIM_MAX_AGE="${BTT_WIDGET_DIM_MAX_AGE:-10}"

# Optional icon, so that a dimmed frame keeps the widget's icon.
BTT_WIDGET_ICON="${BTT_WIDGET_ICON:-}"


# ---------------------------------------------------------------------------
# Internal: per-widget state files
# ---------------------------------------------------------------------------

btt__cache_file() {
    printf '%s/%s.text' "$BTT_WIDGET_CACHE_DIR" "$BTT_WIDGET_UUID"
}

btt__dim_file() {
    printf '%s/%s.dim' "$BTT_WIDGET_CACHE_DIR" "$BTT_WIDGET_UUID"
}


# ---------------------------------------------------------------------------
# Internal: output BTT widget JSON
# ---------------------------------------------------------------------------

btt__emit_json() {
    local text="${1-}"
    local color="${2:-$BTT_WIDGET_COLOR}"
    local icon="${BTT_WIDGET_ICON:-}"

    if [[ -n "$icon" && -f "$icon" ]] && command -v jq >/dev/null 2>&1; then
        jq -cn \
            --arg text "$text" \
            --arg color "$color" \
            --arg icon "$icon" \
            '{text: $text, font_color: $color, icon_path: $icon}'
        return
    fi

    /usr/bin/osascript -l JavaScript - "$text" "$color" <<'JXA'
function run(argv) {
    return JSON.stringify({
        text: argv[0],
        font_color: argv[1]
    });
}
JXA
}


# ---------------------------------------------------------------------------
# Public: remember the text a widget is displaying
#
# actions/tap-refresh.sh cannot know what a widget shows, so every widget
# records its own last value here.
# ---------------------------------------------------------------------------

btt_remember() {
    local result="${1-}"

    [[ -z "${BTT_WIDGET_UUID:-}" ]] && return 0

    mkdir -p "$BTT_WIDGET_CACHE_DIR" 2>/dev/null || return 0

    printf '%s' "$result" > "$(btt__cache_file)" 2>/dev/null || true

    return 0
}


# ---------------------------------------------------------------------------
# Public: serve a manual refresh's "in flight" frame
#
# Return:
#   0 -> this run was the dim frame; the caller must exit without working
#   1 -> ordinary run; the caller should compute its value as usual
# ---------------------------------------------------------------------------

btt_dim_gate() {
    [[ -z "${BTT_WIDGET_UUID:-}" ]] && return 1

    local flag
    flag="$(btt__dim_file)"

    [[ -f "$flag" ]] || return 1

    #
    # Consume the flag first: whatever happens next, this widget must not
    # come up grey again on the following tick.
    #
    local age
    age="$(
        /usr/bin/find "$flag" -mtime -"${BTT_WIDGET_DIM_MAX_AGE}"s 2>/dev/null
    )"

    rm -f "$flag" 2>/dev/null

    # Flag left behind by a refresh that never ran.
    [[ -n "$age" ]] || return 1

    local last=""
    local cache
    cache="$(btt__cache_file)"

    [[ -f "$cache" ]] && last="$(cat "$cache" 2>/dev/null)"

    # Nothing published yet: there is no value to grey out.
    [[ -n "$last" ]] || return 1

    btt__emit_json "$last" "$BTT_WIDGET_DIM_COLOR"

    return 0
}


# ---------------------------------------------------------------------------
# Public: publish a finished widget value
#
# Terminal:
#   prints plain text.
#
# BTT:
#   remembers the result and emits widget JSON in the normal color.
#   The color is always stated explicitly, so that the grey frame from a
#   manual refresh is cleared when the real value arrives.
# ---------------------------------------------------------------------------

btt_publish() {
    local result="${1-}"

    if [[ -z "${BTT_WIDGET_UUID:-}" ]]; then
        printf '%s\n' "$result"
        return 0
    fi

    btt_remember "$result"
    btt__emit_json "$result" "$BTT_WIDGET_COLOR"
}

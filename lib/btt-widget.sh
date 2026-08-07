#!/usr/bin/env bash

#
# Shared BetterTouchTool Script Widget support.
#
# A widget script sets its identity, then uses the cache/refresh pair:
#
#   BTT_WIDGET_NAME="clash-latency"
#   BTT_WIDGET_REFRESH_MAX_RUN=60
#
#   btt_cache_get <name> <max_age_seconds>
#   btt_cache_put <name> "$value"
#   btt_refresh_detached <name> <max_run_seconds> <command> [args...]
#   btt_force_pending
#   btt_publish "$result"
#   btt_current_color        # for a widget that emits its own JSON
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
# How the grey-out works
# ------------------------------------------------------------------
# A widget's font color can only be set by the JSON the widget script
# itself prints. BTT's update_touch_bar_widget AppleScript command takes
# text, icon_path, sf_symbol_*, icon_data and background_color -- there is
# no font_color and no font_size parameter, so an external process cannot
# grey a widget out directly.
#
# So grey is not a state anybody sets: it is simply what the widget looks
# like while its refresh lock is held. The lock exists for exactly as long
# as the detached refresh runs, which makes the dim frame mean something --
# it lasts as long as the work does, whether that work was started by a tap
# or by an ordinary tick. actions/tap-refresh.sh only drops a force flag and
# asks BTT to re-run the widget; the widget does the rest.
#
# The flags are files rather than BTT variables so that the common case --
# an ordinary tick with nothing in flight -- costs a stat() instead of an
# osascript round trip.
#

BTT_WIDGET_CACHE_DIR="${BTT_WIDGET_CACHE_DIR:-$HOME/Library/Caches/btt-widgets}"

# The widget's own name, used to find its value and refresh lock.
BTT_WIDGET_NAME="${BTT_WIDGET_NAME:-}"

# How long this widget's refresh may run before the lock is presumed dead.
BTT_WIDGET_REFRESH_MAX_RUN="${BTT_WIDGET_REFRESH_MAX_RUN:-180}"

# The normal, undimmed label color.
BTT_WIDGET_COLOR="${BTT_WIDGET_COLOR:-255,255,255,255}"

# The color shown while a refresh is in flight.
BTT_WIDGET_DIM_COLOR="${BTT_WIDGET_DIM_COLOR:-130,130,130,255}"

# A force flag older than this belongs to a tap whose refresh never ran.
BTT_WIDGET_FORCE_MAX_AGE="${BTT_WIDGET_FORCE_MAX_AGE:-10}"

# Optional icon, so that a dimmed frame keeps the widget's icon.
BTT_WIDGET_ICON="${BTT_WIDGET_ICON:-}"

# Trace file shared by every widget, read by
# `now_playing_lyrics.py --report`. Set to 0 to turn tracing off.
BTT_WIDGET_TRACE="${BTT_WIDGET_TRACE:-1}"
BTT_WIDGET_TRACE_MAX_BYTES="${BTT_WIDGET_TRACE_MAX_BYTES:-2000000}"

# EPOCHREALTIME gives sub-second timestamps without forking `date`.
if [[ -n "${ZSH_VERSION:-}" ]]; then
    zmodload zsh/datetime 2>/dev/null || true
fi


# ---------------------------------------------------------------------------
# Internal: per-widget state files
# ---------------------------------------------------------------------------

btt__force_file() {
    printf '%s/%s.force' "$BTT_WIDGET_CACHE_DIR" "$BTT_WIDGET_UUID"
}

btt__value_file() {
    printf '%s/%s.value' "$BTT_WIDGET_CACHE_DIR" "$1"
}

btt__refresh_lock() {
    printf '%s/%s.refreshing' "$BTT_WIDGET_CACHE_DIR" "$1"
}

# Younger than max_age?
btt__is_fresh() {
    [[ -n "$(/usr/bin/find "$1" -maxdepth 0 -mtime -"${2}"s 2>/dev/null)" ]]
}


# ---------------------------------------------------------------------------
# Public: leave a record of this run
#
# A frozen Touch Bar looks the same whatever caused it, and by the time
# anyone looks the evidence is gone. So every widget records that it ran, how
# long it took and what it decided. The gaps between records are the useful
# part: they are the runs BTT did not make. Whether every widget stopped at
# once, or only one did, is what separates a BTT problem from a script one --
# which is why all of them share a single file.
#
#   btt_now                            -> seconds, as a float
#   btt_trace <mode> <started> <outcome> [extra]
#
# Columns match the ones now_playing_lyrics.py writes, so a single
# `now_playing_lyrics.py --report` covers every widget.
# ---------------------------------------------------------------------------

btt_now() {
    if [[ -n "${EPOCHREALTIME:-}" ]]; then
        printf '%s' "$EPOCHREALTIME"
    else
        printf '%s' "$(/bin/date +%s)"
    fi
}

btt_trace() {
    [[ "$BTT_WIDGET_TRACE" == "0" ]] && return 0

    local mode="${1-}"
    local started="${2:-0}"
    local outcome="${3-}"
    local extra="${4-}"

    local now
    now="$(btt_now)"

    # One record is one line: a widget value can be multi-line (the quota
    # widgets stack two rows), and pasting one into a column unsanitised
    # would silently corrupt every later reader.
    outcome="${outcome//[$'\t\n\r']/ }"
    extra="${extra//[$'\t\n\r']/ }"

    local file="$BTT_WIDGET_CACHE_DIR/trace.tsv"
    mkdir -p "$BTT_WIDGET_CACHE_DIR" 2>/dev/null || return 0

    printf '%s\t%s\t%s\t%.0f\t%s\t%s\n' \
        "$now" "${BTT_WIDGET_NAME:-unnamed}" "$mode" \
        "$(( (now - started) * 1000 ))" "$outcome" "$extra" \
        >> "$file" 2>/dev/null || return 0

    # These widgets tick every 30-60s, so checking the size each time costs
    # nothing worth avoiding. One generation is kept, as for the lyrics trace.
    local size
    size="$(/usr/bin/stat -f%z "$file" 2>/dev/null || printf '0')"
    if (( size > BTT_WIDGET_TRACE_MAX_BYTES )); then
        mv -f "$file" "$file.1" 2>/dev/null || true
    fi

    return 0
}


# ---------------------------------------------------------------------------
# Why slow widgets freeze every other widget
#
# BTT ships a single BetterTouchToolShellScriptRunner XPC service and every
# shell script widget goes through it, so a widget that blocks for n seconds
# stops every other widget for n seconds -- and swallows their tap-refreshes
# too. Measured here: codexbar's Claude lookup takes ~43s, during which the
# 1s lyrics widget simply does not run.
#
# So anything that touches the network is computed by a detached refresh that
# stores its result, and the widget path only ever reads that stored value.
#
#   btt_cache_get <name> <max_age_seconds>
#       Print the stored value. Returns 0 when it is still fresh, 1 when it
#       is missing or too old. A stale value is still printed, so the widget
#       has something to show while the refresh runs.
#
#   btt_cache_put <name> "$value"
#       Store a newly computed value.
#
#   btt_refresh_detached <name> <max_run_seconds> <command> [args...]
#       Run <command> detached, at most one at a time, and redraw the widget
#       once it is done.
# ---------------------------------------------------------------------------

btt_cache_get() {
    local name="${1-}"
    local max_age="${2:-60}"
    local file
    file="$(btt__value_file "$name")"

    [[ -f "$file" ]] || return 1

    cat "$file" 2>/dev/null

    btt__is_fresh "$file" "$max_age"
}

btt_cache_put() {
    local name="${1-}"
    local value="${2-}"

    mkdir -p "$BTT_WIDGET_CACHE_DIR" 2>/dev/null || return 1

    local file
    file="$(btt__value_file "$name")"

    # Written aside and moved into place, so a widget run can never read a
    # half-written value.
    printf '%s' "$value" > "$file.$$" 2>/dev/null || return 1
    mv -f "$file.$$" "$file" 2>/dev/null || return 1
}

btt_refresh_detached() {
    local name="${1-}"
    local max_run="${2:-120}"
    shift 2 || return 1

    mkdir -p "$BTT_WIDGET_CACHE_DIR" 2>/dev/null || return 1

    local lock
    lock="$(btt__refresh_lock "$name")"

    # A lock older than the refresh could possibly take belongs to a run that
    # died; nothing else would still be holding it.
    if [[ -d "$lock" ]] && ! btt__is_fresh "$lock" "$max_run"; then
        rmdir "$lock" 2>/dev/null
    fi

    # mkdir is the atomic "create only if absent": whoever wins refreshes,
    # everyone else leaves it alone.
    mkdir "$lock" 2>/dev/null || return 1

    local uuid="${BTT_WIDGET_UUID:-}"

    # The redirections are on the subshell, not on the command inside it.
    # A background child that keeps the widget's stdout open holds the pipe
    # open too, and whoever is reading that pipe -- BTT -- waits for it, which
    # is the very blocking this function exists to avoid.
    #
    # The lock is dropped BEFORE the redraw is requested. The redraw runs the
    # widget, and the widget colors itself by whether the lock is held: asking
    # first would paint the new value grey and leave it grey until the next
    # tick.
    (
        trap '' HUP
        "$@"
        rmdir "$lock" 2>/dev/null
        btt_request_refresh "$uuid"
    ) </dev/null >/dev/null 2>&1 &
    disown 2>/dev/null || true

    return 0
}


# ---------------------------------------------------------------------------
# Public: ask BTT to redraw a widget now
#
# Called after a detached refresh so a new value appears immediately instead
# of at the widget's next scheduled tick. That is what lets a slow widget be
# given a long refresh interval without feeling stale.
# ---------------------------------------------------------------------------

btt_request_refresh() {
    local uuid="${1-}"

    [[ -z "$uuid" ]] && return 0

    /usr/bin/osascript -l JavaScript - "$uuid" >/dev/null 2>&1 <<'JXA' || true
function run(argv) {
    Application("BetterTouchTool").refresh_widget(argv[0]);
}
JXA
}


# ---------------------------------------------------------------------------
# Public: did a tap ask for a refresh?
#
# Consumed on read: the flag orders one refresh, not a mode the widget stays
# in. A stale flag is dropped rather than obeyed, so a tap whose refresh
# never ran cannot trigger one minutes later.
#
# Return:
#   0 -> refresh regardless of how fresh the cached value is
#   1 -> ordinary run
# ---------------------------------------------------------------------------

btt_force_pending() {
    [[ -z "${BTT_WIDGET_UUID:-}" ]] && return 1

    local flag
    flag="$(btt__force_file)"

    [[ -f "$flag" ]] || return 1

    local fresh=1
    btt__is_fresh "$flag" "$BTT_WIDGET_FORCE_MAX_AGE" || fresh=0

    rm -f "$flag" 2>/dev/null

    (( fresh ))
}


# ---------------------------------------------------------------------------
# Public: is this widget's refresh running right now?
# ---------------------------------------------------------------------------

btt_refresh_in_flight() {
    local name="${1:-$BTT_WIDGET_NAME}"

    [[ -z "$name" ]] && return 1

    local lock
    lock="$(btt__refresh_lock "$name")"

    [[ -d "$lock" ]] || return 1

    btt__is_fresh "$lock" "$BTT_WIDGET_REFRESH_MAX_RUN"
}


# ---------------------------------------------------------------------------
# Public: the color this widget should render in right now
#
# For a widget that builds its own JSON. btt_publish applies this itself.
# ---------------------------------------------------------------------------

btt_current_color() {
    if btt_refresh_in_flight; then
        printf '%s' "$BTT_WIDGET_DIM_COLOR"
    else
        printf '%s' "$BTT_WIDGET_COLOR"
    fi
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
# Public: publish a finished widget value
#
# Terminal:
#   prints plain text.
#
# BTT:
#   emits widget JSON, dimmed while this widget's refresh is in flight.
#   The color is always stated explicitly, so that a dimmed frame is cleared
#   when the refresh finishes.
# ---------------------------------------------------------------------------

btt_publish() {
    local result="${1-}"

    if [[ -z "${BTT_WIDGET_UUID:-}" ]]; then
        printf '%s\n' "$result"
        return 0
    fi

    btt__emit_json "$result" "$(btt_current_color)"
}

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

BTT_REPO_DIR="${BTT_REPO_DIR:-$HOME/Documents/BTT}"
BTT_WIDGET_CACHE_DIR="${BTT_WIDGET_CACHE_DIR:-$BTT_REPO_DIR/cache}"
BTT_WIDGET_LOG_DIR="${BTT_WIDGET_LOG_DIR:-${BTT_LOG_DIR:-$BTT_REPO_DIR/logs}}"

# The widget's own name, used to find its value and refresh lock.
BTT_WIDGET_NAME="${BTT_WIDGET_NAME:-}"

# How long this widget's refresh may run before the lock is presumed dead.
BTT_WIDGET_REFRESH_MAX_RUN="${BTT_WIDGET_REFRESH_MAX_RUN:-180}"

# The normal, undimmed label color.
BTT_WIDGET_COLOR="${BTT_WIDGET_COLOR:-255,255,255,255}"

# The color shown while a refresh is in flight.
BTT_WIDGET_DIM_COLOR="${BTT_WIDGET_DIM_COLOR:-125,125,125,255}"

# The dim color used when a quota is exhausted and waiting for reset.
BTT_WIDGET_QUOTA_DIM_COLOR="${BTT_WIDGET_QUOTA_DIM_COLOR:-150,150,150,255}"

# Set by quota widgets to enable reset-progress coloring.
BTT_WIDGET_QUOTA_RESET_CYCLE_MINUTES="${BTT_WIDGET_QUOTA_RESET_CYCLE_MINUTES:-0}"

# A force flag older than this belongs to a tap whose refresh never ran.
BTT_WIDGET_FORCE_MAX_AGE="${BTT_WIDGET_FORCE_MAX_AGE:-10}"

# Optional icon, so that a dimmed frame keeps the widget's icon.
BTT_WIDGET_ICON="${BTT_WIDGET_ICON:-}"

# Trace file shared by every widget, read by
# `widgets/now-playing-lyrics.sh --report`. Set to 0 to turn tracing off.
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

btt__quota_reset_file() {
    printf '%s/%s.quota-reset' "$BTT_WIDGET_CACHE_DIR" "$1"
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
# Columns match the ones the lyrics widget writes, so a single
# `widgets/now-playing-lyrics.sh --report` covers every widget.
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

    local file="${BTT_WIDGET_TRACE_FILE:-$BTT_WIDGET_LOG_DIR/trace.tsv}"
    mkdir -p "${file:h}" 2>/dev/null || return 0

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

# ---------------------------------------------------------------------------
# Public: run a command fully detached, in its own session
#
# `& disown` alone only drops a job from the shell's job table -- in a
# non-interactive shell (which every BTT script widget or action is), job
# control can't be turned on to give the job a process group of its own;
# `setopt monitor` fails outright without a controlling terminal. So a
# background child started with plain `cmd & disown` stays in the very
# process group BTT launched for this script. If BTT's single script-runner
# XPC service ever waits on or signals by process group rather than just the
# one pid it started, a slow or wedged child can block every future
# invocation of this same script behind it -- for however long after the
# visible script already returned. Measured: a lyrics helper backgrounded
# this way, stuck on an unresponsive Music.app AppleScript call, silently
# froze the lyrics widget's own ordinary ticks for as long as 26 minutes.
#
# A launchd job would dodge that, but was tried and rejected: it runs as a
# separately-authorized process, and macOS's TCC then blocks it from this
# repo's files under ~/Documents ("operation not permitted"), even though the
# very same script runs fine spawned directly. So instead a tiny Python
# double-fork calls setsid() on the child, which moves it into a session and
# process group of its own while keeping it in the same
# BTT -> zsh -> python -> target process lineage BTT already has file access
# for.
#
# Any caller that backgrounds work with a raw `cmd </dev/null >/dev/null
# 2>&1 &` should use this instead -- see actions/track-changed.sh and
# actions/lyrics-force-refresh.sh, both of which call into Apple Music and
# used to skip this.
# ---------------------------------------------------------------------------

btt_spawn_detached() {
    (
        /usr/bin/python3 -c '
import os, sys
if os.fork() != 0:
    os._exit(0)
os.setsid()
os.execvp(sys.argv[1], sys.argv[1:])
' "$@"
    ) </dev/null >/dev/null 2>&1 &
    disown 2>/dev/null || true
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

    # The lock is dropped BEFORE the redraw is requested. The redraw runs the
    # widget, and the widget colors itself by whether the lock is held: asking
    # first would paint the new value grey and leave it grey until the next
    # tick.
    btt_spawn_detached /bin/zsh -c '
            trap "" HUP
            lock="$1"; uuid="$2"; shift 2
            "$@"
            rmdir "$lock" 2>/dev/null
            if [[ -n "$uuid" ]]; then
                for delay in 0 0.5 2; do
                    (( delay > 0 )) && /bin/sleep "$delay"
                    /usr/bin/osascript -e "tell application \"BetterTouchTool\" to refresh_widget \"$uuid\"" >/dev/null 2>&1
                done
            fi
        ' refresh-wrapper "$lock" "$uuid" "$@"

    return 0
}


# ---------------------------------------------------------------------------
# Public: run a cached widget with detached refresh work
#
# The caller supplies its refresh-mode flag, absolute script path, cache age,
# and a callback that prints the refreshed value. The shared lifecycle keeps
# cache, forced-refresh, trace, and output behavior consistent.
# ---------------------------------------------------------------------------

btt_cached_widget_main() {
    local refresh_mode="${1:-0}"
    local self="${2-}"
    local value_max_age="${3:-300}"
    local compute_value="${4-}"
    local started
    started="$(btt_now)"

    if (( refresh_mode )); then
        local refreshed
        refreshed="$("$compute_value")"
        btt_cache_put "$BTT_WIDGET_NAME" "$refreshed"

        local refresh_outcome
        case "$refreshed" in
            "")            refresh_outcome=empty ;;
            *ERR*|NO\ *)   refresh_outcome=error ;;
            *)             refresh_outcome=ok ;;
        esac
        btt_trace refresh "$started" "$refresh_outcome" "value=$refreshed"
        return 0
    fi

    local value fresh force outcome
    value="$(btt_cache_get "$BTT_WIDGET_NAME" "$value_max_age")"
    fresh=$?

    force=0
    if btt_force_pending; then
        force=1
    fi

    if (( fresh != 0 || force )); then
        btt_refresh_detached \
            "$BTT_WIDGET_NAME" "$BTT_WIDGET_REFRESH_MAX_RUN" \
            "$self" --refresh "$BTT_WIDGET_UUID"
    fi

    outcome=cached
    (( fresh != 0 )) && outcome=stale
    (( force )) && outcome=forced
    [[ -n "$value" ]] || outcome=empty
    btt_trace widget "$started" "$outcome"
    btt_publish "${value:-…}"
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
# Public: persist the exact reset time alongside a quota value
# ---------------------------------------------------------------------------

btt_quota_reset_put() {
    local name="${1-}"
    local reset_at="${2-}"
    local file
    file="$(btt__quota_reset_file "$name")"

    mkdir -p "$BTT_WIDGET_CACHE_DIR" 2>/dev/null || return 1

    if [[ -z "$reset_at" ]]; then
        rm -f "$file" 2>/dev/null
        return 0
    fi

    printf '%s' "$reset_at" > "$file.$$" 2>/dev/null || return 1
    mv -f "$file.$$" "$file" 2>/dev/null || return 1
}

btt_quota_reset_get() {
    local name="${1:-$BTT_WIDGET_NAME}"
    local file
    file="$(btt__quota_reset_file "$name")"
    [[ -f "$file" ]] || return 1
    cat "$file" 2>/dev/null
}


# ---------------------------------------------------------------------------
# Public: interpolate the configured widget color range
# ---------------------------------------------------------------------------

btt_color_at_progress() {
    local progress="${1:-100}"
    (( progress < 0 )) && progress=0
    (( progress > 100 )) && progress=100

    local dim_color="$BTT_WIDGET_QUOTA_DIM_COLOR"
    local normal_color="$BTT_WIDGET_COLOR"
    local dim_r="${dim_color%%,*}"
    local dim_rest="${dim_color#*,}"
    local dim_g="${dim_rest%%,*}"
    dim_rest="${dim_rest#*,}"
    local dim_b="${dim_rest%%,*}"
    local normal_r="${normal_color%%,*}"
    local normal_rest="${normal_color#*,}"
    local normal_g="${normal_rest%%,*}"
    normal_rest="${normal_rest#*,}"
    local normal_b="${normal_rest%%,*}"
    local normal_a="${normal_rest#*,}"

    printf '%d,%d,%d,%s' \
        "$(( dim_r + (normal_r - dim_r) * progress / 100 ))" \
        "$(( dim_g + (normal_g - dim_g) * progress / 100 ))" \
        "$(( dim_b + (normal_b - dim_b) * progress / 100 ))" \
        "$normal_a"
}


# ---------------------------------------------------------------------------
# Public: color a quota by usage or its progress toward reset
# ---------------------------------------------------------------------------

btt_quota_color() {
    local text="${1-}"
    local cycle="${BTT_WIDGET_QUOTA_RESET_CYCLE_MINUTES:-0}"
    local secondary_cycle="${BTT_WIDGET_QUOTA_SECONDARY_RESET_CYCLE_MINUTES:-0}"

    if (( cycle <= 0 && secondary_cycle <= 0 )); then
        printf '%s' "$BTT_WIDGET_COLOR"
        return 0
    fi

    local first_line="${text%%$'\n'*}"
    local used_percent="${first_line%%\%*}"
    local selected_line="$first_line"
    local reset_name="$BTT_WIDGET_NAME"

    if (( secondary_cycle > 0 )) && [[ "$text" == *$'\n'* ]]; then
        local secondary_line="${text#*$'\n'}"
        secondary_line="${secondary_line%%$'\n'*}"
        local secondary_used="${secondary_line%%\%*}"
        if [[ "$secondary_used" =~ '^[0-9]+$' ]] && (( secondary_used >= 100 )); then
            used_percent="$secondary_used"
            cycle="$secondary_cycle"
            selected_line="$secondary_line"
            reset_name="${BTT_WIDGET_NAME}-secondary"
        fi
    fi

    if (( cycle <= 0 )) || [[ ! "$used_percent" =~ '^[0-9]+$' ]]; then
        printf '%s' "$BTT_WIDGET_COLOR"
        return 0
    fi

    local progress
    if (( used_percent < 100 )); then
        progress=$(( 100 - used_percent ))
    else
        local remaining_label="${selected_line#*%}"
        if [[ -z "$remaining_label" ]]; then
            remaining_label="${text#*$'\n'}"
            remaining_label="${remaining_label%%$'\n'*}"
        else
            remaining_label="${remaining_label// /}"
        fi

        local remaining_minutes=0
        local reset_at
    reset_at="$(btt_quota_reset_get "$reset_name")"
        if [[ "$reset_at" =~ '^[0-9]+$' ]]; then
            local now_seconds
            now_seconds="$(btt_now)"
            now_seconds="${now_seconds%%.*}"
            remaining_minutes=$(( (reset_at - now_seconds) / 60 ))
        else
            case "$remaining_label" in
                '<1m') remaining_minutes=0 ;;
                *d) remaining_minutes=$(( ${remaining_label%d} * 1440 )) ;;
                *h) remaining_minutes=$(( ${remaining_label%h} * 60 )) ;;
                *m) remaining_minutes=$(( ${remaining_label%m} )) ;;
            esac
        fi

        (( remaining_minutes < 0 )) && remaining_minutes=0
        (( remaining_minutes > cycle )) && remaining_minutes="$cycle"

        progress=$(( (cycle - remaining_minutes) * 100 / cycle ))
    fi

    btt_color_at_progress "$progress"
}


# ---------------------------------------------------------------------------
# Public: color latency from the configured minimum to maximum
# ---------------------------------------------------------------------------

btt_latency_color() {
    local text="${1-}"
    local min_ms="${BTT_WIDGET_LATENCY_MIN_MS:-150}"
    local max_ms="${BTT_WIDGET_LATENCY_MAX_MS:-800}"
    local first_line="${text%%$'\n'*}"
    local latency="${first_line%%ms*}"

    if [[ ! "$min_ms" =~ '^[0-9]+$' || ! "$max_ms" =~ '^[0-9]+$' ]] || \
        (( max_ms <= min_ms )); then
        printf '%s' "$BTT_WIDGET_COLOR"
        return 0
    fi

    if [[ "$first_line" == "Timeout" ]]; then
        btt_color_at_progress 0
        return 0
    fi

    if [[ ! "$latency" =~ '^[0-9]+$' ]]; then
        printf '%s' "$BTT_WIDGET_COLOR"
        return 0
    fi

    local progress
    if (( latency <= min_ms )); then
        progress=100
    elif (( latency >= max_ms )); then
        progress=0
    else
        progress=$(( (max_ms - latency) * 100 / (max_ms - min_ms) ))
    fi

    btt_color_at_progress "$progress"
}


# ---------------------------------------------------------------------------
# Public: the color this widget should render in right now
#
# For a widget that builds its own JSON. btt_publish applies this itself.
# ---------------------------------------------------------------------------

btt_current_color() {
    local text="${1-}"

    if btt_refresh_in_flight; then
        printf '%s' "$BTT_WIDGET_DIM_COLOR"
    elif [[ -n "${BTT_WIDGET_LATENCY_MIN_MS:-}" &&
        -n "${BTT_WIDGET_LATENCY_MAX_MS:-}" ]]; then
        btt_latency_color "$text"
    elif (( BTT_WIDGET_QUOTA_RESET_CYCLE_MINUTES > 0 )); then
        btt_quota_color "$text"
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

    btt__emit_json "$result" "$(btt_current_color "$result")"
}


#!/usr/bin/env bash

#
# Shared BetterTouchTool Script Widget support.
#
# Public functions:
#
#   btt_refresh_gate "$0" "$@"
#       Call near the beginning of a widget script.
#
#   btt_publish "$result"
#       Call instead of echo/printf for the final widget result.
#
#
# BTT mode:
#   BTT_WIDGET_UUID must be set.
#
# Terminal mode:
#   BTT_WIDGET_UUID may be unset.
#   The script behaves like an ordinary shell script.
#


# ---------------------------------------------------------------------------
# Internal: output BTT widget JSON
# ---------------------------------------------------------------------------

btt__emit_json() {
    local text="${1-}"
    local color="${2:-255,255,255,255}"

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
# Internal: retrieve last successfully published text
# ---------------------------------------------------------------------------

btt__get_last_result() {
    /usr/bin/osascript -l JavaScript - "$BTT_WIDGET_UUID" <<'JXA'
function run(argv) {
    const uuid = argv[0];
    const btt = Application("BetterTouchTool");

    const value = btt.get_string_variable(
        `btt.widget.${uuid}.last_result`
    );

    return value || "—";
}
JXA
}


# ---------------------------------------------------------------------------
# Internal: make script path absolute so it can spawn itself
# ---------------------------------------------------------------------------

btt__absolute_path() {
    local path="$1"

    if [[ "$path" = /* ]]; then
        printf '%s\n' "$path"
        return
    fi

    local dir
    local base

    dir="$(dirname "$path")"
    base="$(basename "$path")"

    (
        cd "$dir" 2>/dev/null || exit 1
        printf '%s/%s\n' "$PWD" "$base"
    )
}


# ---------------------------------------------------------------------------
# Internal: recover if a background refresh crashes before btt_publish()
# ---------------------------------------------------------------------------

btt__worker_cleanup() {
    local status="${1:-1}"

    if [[ "${BTT_PUBLISHED:-0}" == "1" ]]; then
        return
    fi

    [[ -z "${BTT_WIDGET_UUID:-}" ]] && return

    /usr/bin/osascript -l JavaScript - \
        "$BTT_WIDGET_UUID" <<'JXA' >/dev/null 2>&1
function run(argv) {
    const uuid = argv[0];
    const btt = Application("BetterTouchTool");
    const prefix = `btt.widget.${uuid}.`;

    btt.set_string_variable(
        prefix + "refreshing",
        {to: "0"}
    );

    btt.set_string_variable(
        prefix + "worker_running",
        {to: "0"}
    );

    btt.set_string_variable(
        prefix + "refresh_started_ms",
        {to: "0"}
    );

    // Re-render the old cached result in white.
    btt.set_string_variable(
        prefix + "ready",
        {to: "1"}
    );

    btt.refresh_widget(uuid);
}
JXA

    return "$status"
}


# ---------------------------------------------------------------------------
# Public: intercept manual refresh states
#
# Usage:
#
#   if btt_refresh_gate "$0" "$@"; then
#       exit 0
#   fi
#
# Return:
#   0 -> library handled this invocation; caller should exit
#   1 -> caller should perform its normal computation
# ---------------------------------------------------------------------------

btt_refresh_gate() {
    local script_path="${1-}"
    shift || true

    #
    # Terminal mode:
    # just run the script normally.
    #
    if [[ -z "${BTT_WIDGET_UUID:-}" ]]; then
        return 1
    fi

    #
    # Background worker:
    # bypass the display gate and perform the actual computation.
    #
    if [[ "${BTT_REFRESH_WORKER:-0}" == "1" ]]; then
        BTT_PUBLISHED=0
        trap 'btt__worker_cleanup "$?"' EXIT
        return 1
    fi

    #
    # Ask BTT which state this widget is currently in.
    #
    local mode

    mode="$(
        /usr/bin/osascript -l JavaScript - \
            "$BTT_WIDGET_UUID" <<'JXA'
function run(argv) {
    const uuid = argv[0];
    const btt = Application("BetterTouchTool");
    const prefix = `btt.widget.${uuid}.`;

    const refreshing =
        btt.get_string_variable(prefix + "refreshing") || "0";

    const ready =
        btt.get_string_variable(prefix + "ready") || "0";

    const workerRunning =
        btt.get_string_variable(prefix + "worker_running") || "0";


    // A background refresh has completed.
    // Consume the ready state exactly once.
    if (ready === "1") {
        btt.set_string_variable(
            prefix + "ready",
            {to: "0"}
        );

        return "ready";
    }


    // Manual refresh is currently active.
    if (refreshing === "1") {

        // Only one invocation is allowed to launch the worker.
        if (workerRunning !== "1") {
            btt.set_string_variable(
                prefix + "worker_running",
                {to: "1"}
            );

            return "refresh-start";
        }

        return "refresh-wait";
    }


    return "normal";
}
JXA
    )"

    case "$mode" in

        ready)
            #
            # Worker has produced a new cached value.
            # Display it immediately in white.
            #
            local last
            last="$(btt__get_last_result)"

            btt__emit_json "$last" "255,255,255,255"

            return 0
            ;;


        refresh-start)
            #
            # Display OLD cached result in grey,
            # while the same widget script runs independently.
            #

            local last
            last="$(btt__get_last_result)"

            local absolute_script
            absolute_script="$(btt__absolute_path "$script_path")" || {
                btt__emit_json "$last" "255,255,255,255"
                return 0
            }

            #
            # Run THIS SAME SCRIPT as the worker.
            #
            # All existing environment variables are inherited.
            #
            nohup /usr/bin/env \
                BTT_REFRESH_WORKER=1 \
                BTT_WIDGET_UUID="$BTT_WIDGET_UUID" \
                "$absolute_script" "$@" \
                >/dev/null 2>&1 &

            #
            # Current BTT invocation finishes immediately.
            #
            btt__emit_json "$last" "130,130,130,255"

            return 0
            ;;


        refresh-wait)
            #
            # Another periodic refresh happened while the worker
            # was already running. Do NOT launch another one.
            #
            local last
            last="$(btt__get_last_result)"

            btt__emit_json "$last" "130,130,130,255"

            return 0
            ;;


        *)
            #
            # Ordinary scheduled/initial BTT execution.
            #
            return 1
            ;;

    esac
}


# ---------------------------------------------------------------------------
# Public: publish a finished widget value
#
# Terminal:
#   prints plain text.
#
# Normal BTT invocation:
#   caches result and returns white widget JSON.
#
# Refresh worker:
#   caches result, switches state to ready and refreshes BTT.
# ---------------------------------------------------------------------------

btt_publish() {
    local result="${1-}"

    #
    # Terminal mode.
    #
    if [[ -z "${BTT_WIDGET_UUID:-}" ]]; then
        printf '%s\n' "$result"
        return 0
    fi


    #
    # Background manual-refresh worker.
    #
    if [[ "${BTT_REFRESH_WORKER:-0}" == "1" ]]; then

        /usr/bin/osascript -l JavaScript - \
            "$BTT_WIDGET_UUID" \
            "$result" <<'JXA' >/dev/null
function run(argv) {
    const uuid = argv[0];
    const result = argv[1];

    const btt = Application("BetterTouchTool");
    const prefix = `btt.widget.${uuid}.`;

    // Store last successful display value persistently.
    btt.set_persistent_string_variable(
        prefix + "last_result",
        {to: result}
    );

    // Worker is finished.
    btt.set_string_variable(
        prefix + "refreshing",
        {to: "0"}
    );

    btt.set_string_variable(
        prefix + "worker_running",
        {to: "0"}
    );

    btt.set_string_variable(
        prefix + "refresh_started_ms",
        {to: "0"}
    );

    // Next widget invocation should only render this result,
    // not calculate it a second time.
    btt.set_string_variable(
        prefix + "ready",
        {to: "1"}
    );

    btt.refresh_widget(uuid);
}
JXA

        BTT_PUBLISHED=1
        return 0
    fi


    #
    # Ordinary scheduled/initial BTT execution.
    #
    # Cache and return white JSON in one call.
    #
    /usr/bin/osascript -l JavaScript - \
        "$BTT_WIDGET_UUID" \
        "$result" <<'JXA'
function run(argv) {
    const uuid = argv[0];
    const result = argv[1];

    const btt = Application("BetterTouchTool");

    btt.set_persistent_string_variable(
        `btt.widget.${uuid}.last_result`,
        {to: result}
    );

    return JSON.stringify({
        text: result,
        font_color: "255,255,255,255"
    });
}
JXA
}
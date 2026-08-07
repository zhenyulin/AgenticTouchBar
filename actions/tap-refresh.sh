#!/usr/bin/env bash

set -euo pipefail

BTT_WIDGET_UUID="${1:?widget UUID required}"

# A refresh lock older than this is considered dead.
STALE_AFTER_SECONDS="${BTT_REFRESH_STALE_AFTER:-30}"

/usr/bin/osascript -l JavaScript - \
    "$BTT_WIDGET_UUID" \
    "$STALE_AFTER_SECONDS" <<'JXA' >/dev/null

function run(argv) {
    const uuid = argv[0];
    const staleAfterMs = Number(argv[1]) * 1000;

    const btt = Application("BetterTouchTool");
    const p = `btt.widget.${uuid}.`;

    const refreshing =
        btt.get_string_variable(p + "refreshing") || "0";

    const startedMs =
        Number(
            btt.get_string_variable(p + "refresh_started_ms") || "0"
        );

    const now = Date.now();
    const ageMs = now - startedMs;

    /*
     * If a refresh is genuinely still running, ignore duplicate taps.
     *
     * But NEVER allow the lock to live forever. If BTT missed a
     * refresh_widget call, the worker crashed, etc., the next tap after
     * the timeout automatically recovers.
     */
    if (
        refreshing === "1" &&
        btt.get_string_variable(p + "worker_running") === "1" &&
        startedMs > 0 &&
        ageMs >= 0 &&
        ageMs < staleAfterMs
    ) {
        return "already-refreshing";
    }

    /*
     * Either:
     *   - normal idle state, or
     *   - stale/dead previous refresh.
     *
     * Start a clean generation.
     */
    btt.set_string_variable(
        p + "refreshing",
        {to: "1"}
    );

    btt.set_string_variable(
        p + "worker_running",
        {to: "0"}
    );

    btt.set_string_variable(
        p + "ready",
        {to: "0"}
    );

    btt.set_string_variable(
        p + "refresh_started_ms",
        {to: String(now)}
    );

    btt.refresh_widget(uuid);

    return "started";
}

JXA
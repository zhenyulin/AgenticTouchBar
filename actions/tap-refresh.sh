#!/usr/bin/env bash

set -euo pipefail

# Prefer an explicit argument; retain env var as a fallback.
BTT_WIDGET_UUID="${1:-${BTT_WIDGET_UUID:-}}"

: "${BTT_WIDGET_UUID:?widget UUID required}"

/usr/bin/osascript -l JavaScript - \
    "$BTT_WIDGET_UUID" <<'JXA' >/dev/null
function run(argv) {
    const uuid = argv[0];
    const btt = Application("BetterTouchTool");

    const prefix = `btt.widget.${uuid}.`;

    const refreshing =
        btt.get_string_variable(prefix + "refreshing") || "0";

    // Ignore repeated taps while already refreshing.
    if (refreshing === "1") {
        return;
    }

    btt.set_string_variable(
        prefix + "refreshing",
        {to: "1"}
    );

    btt.set_string_variable(
        prefix + "worker_running",
        {to: "0"}
    );

    btt.set_string_variable(
        prefix + "ready",
        {to: "0"}
    );

    // Rerun this widget's own configured script.
    btt.refresh_widget(uuid);
}
JXA
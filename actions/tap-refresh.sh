#!/usr/bin/env zsh
#
# BetterTouchTool Touch Bar manual refresh helper.
#
# Usage:
#   tap-refresh.sh <widget-uuid>
#
# Behaviour:
#   1. Raise the widget's dim flag and re-run it: the widget reprints its
#      current value in grey and exits at once.
#   2. Ask BTT to refresh the widget for real, in the background.
#   3. Exit immediately.
#
# The grey frame comes from the widget script itself, via btt_dim_gate in
# lib/btt-widget.sh. It cannot be painted from here: BTT's
# update_touch_bar_widget command only accepts text, icon_path, sf_symbol_*,
# icon_data and background_color -- there is no font_color parameter, and
# passing one has no effect beyond re-rendering the widget in its configured
# style (which shows up as the label changing size).
#
# The widget script is responsible for its own command/network timeouts.
#
# BTT is driven through AppleScript rather than its btt:// URL scheme,
# because the URL scheme requires BTT's "URL scripting" permission and
# prompts for it on every invocation.
#

set -u

PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

UUID="${1:-${BTT_WIDGET_UUID:-}}"

if [[ -z "$UUID" ]]; then
    exit 1
fi

CACHE_DIR="${BTT_WIDGET_CACHE_DIR:-$HOME/Library/Caches/btt-widgets}"

# Immediate visual acknowledgement.
#
# Failure here must NOT prevent the actual refresh request.
#
# This refresh is deliberately NOT detached: the widget's dim run only
# reprints a cached string, so it returns in milliseconds, and waiting for
# it is what guarantees the flag is consumed by the dim frame rather than by
# the real refresh below.
if mkdir -p "$CACHE_DIR" 2>/dev/null; then
    : > "$CACHE_DIR/$UUID.dim" 2>/dev/null || true

    /usr/bin/osascript -l JavaScript - "$UUID" <<'JXA' >/dev/null 2>&1 || true
function run(argv) {
    Application("BetterTouchTool").refresh_widget(argv[0]);
}
JXA
fi

# Fire-and-forget refresh.
#
# refresh_widget blocks until the widget script has finished, and that is
# the slow, real run: it is detached so this BTT shell action returns
# immediately.
nohup /usr/bin/osascript -l JavaScript - "$UUID" <<'JXA' >/dev/null 2>&1 &
function run(argv) {
    const uuid = argv[0];
    const btt = Application("BetterTouchTool");

    btt.refresh_widget(uuid);
}
JXA

exit 0

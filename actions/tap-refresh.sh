#!/usr/bin/env zsh
#
# BetterTouchTool Touch Bar manual refresh helper.
#
# Usage:
#   tap-refresh.sh <widget-uuid>
#
# Behaviour:
#   1. Raise the widget's force flag.
#   2. Ask BTT to re-run the widget.
#   3. Exit immediately.
#
# That run starts the widget's refresh even if its cached value is still
# fresh, and greys itself out because the refresh is now in flight. The grey
# lasts as long as the refresh does, and the redraw that follows it restores
# the normal color -- see widgets/lib/btt-widget.sh.
#
# Nothing is painted from here. BTT's update_touch_bar_widget command only
# accepts text, icon_path, sf_symbol_*, icon_data and background_color: there
# is no font_color parameter, and passing one has no effect beyond
# re-rendering the widget in its configured style (which shows up as the
# label changing size).
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

# Failure to raise the flag must NOT prevent the refresh request: a refresh
# that only picks up a stale value still beats no refresh at all.
if mkdir -p "$CACHE_DIR" 2>/dev/null; then
    : > "$CACHE_DIR/$UUID.force" 2>/dev/null || true
fi

# Fire-and-forget: refresh_widget blocks until the widget script has
# finished, and this BTT shell action must return immediately.
nohup /usr/bin/osascript -l JavaScript - "$UUID" <<'JXA' >/dev/null 2>&1 &
function run(argv) {
    const uuid = argv[0];
    const btt = Application("BetterTouchTool");

    btt.refresh_widget(uuid);
}
JXA

exit 0

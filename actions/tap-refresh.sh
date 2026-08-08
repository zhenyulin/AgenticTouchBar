#!/usr/bin/env zsh
#
# BetterTouchTool Touch Bar manual refresh helper.
#
# Usage:
#   tap-refresh.sh [--mark-only] <widget-uuid> [<widget-uuid> ...]
#
# With --mark-only, this script only raises the force flags. That mode is for
# BTT Touch Bar actions, whose following Real JavaScript action performs the
# native refresh_widget call without launching osascript from the shell runner.
# Without --mark-only, the script retains its Terminal/CLI fire-and-forget
# refresh behavior.
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

MARK_ONLY=0
if [[ "${1:-}" == "--mark-only" ]]; then
    MARK_ONLY=1
    shift
fi

if (( $# > 0 )); then
    UUIDS=("$@")
elif [[ -n "${BTT_WIDGET_UUID:-}" ]]; then
    UUIDS=("$BTT_WIDGET_UUID")
else
    UUIDS=()
fi

if (( ${#UUIDS[@]} == 0 )); then
    exit 1
fi

CACHE_DIR="${BTT_WIDGET_CACHE_DIR:-$HOME/Library/Caches/btt-widgets}"

# Failure to raise the flag must NOT prevent the refresh request in normal
# CLI mode: a refresh that only picks up a stale value still beats none. In
# --mark-only mode, the following BTT JavaScript action still runs even if
# writing a flag failed.
if mkdir -p "$CACHE_DIR" 2>/dev/null; then
    for uuid in "${UUIDS[@]}"; do
        : > "$CACHE_DIR/$uuid.force" 2>/dev/null || true
    done
fi

if (( MARK_ONLY )); then
    exit 0
fi

# Fire-and-forget: refresh_widget blocks until the widget script has
# finished, and this BTT shell action must return immediately.
nohup /usr/bin/osascript -l JavaScript - "${UUIDS[@]}" <<'JXA' >/dev/null 2>&1 &
function run(argv) {
    const btt = Application("BetterTouchTool");

    for (const uuid of argv) {
        btt.refresh_widget(uuid);
    }
}
JXA

exit 0

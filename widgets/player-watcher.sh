#!/usr/bin/env zsh

set -u
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

CACHE_DIR="${BTT_WIDGET_CACHE_DIR:-$HOME/Library/Caches/btt-widgets}"
RUN_MARKER="$CACHE_DIR/player-watcher.running"
LYRICS_UUID="E19BB023-5060-4A56-95C8-6E7402779870"
STAR_UUID="A05C5D37-7EAA-4F7B-AC00-23183CC8C6A1"
POLL_INTERVAL_SECONDS="${BTT_PLAYER_WATCH_INTERVAL:-10}"

cleanup() {
    rmdir "$RUN_MARKER" 2>/dev/null || true
}
trap cleanup EXIT HUP INT TERM

[[ -d "$RUN_MARKER" ]] || exit 0

last_signature=""
missing_btt_checks=0
while true; do
    if ! /usr/bin/pgrep -x BetterTouchTool >/dev/null 2>&1; then
        (( missing_btt_checks++ ))
        (( missing_btt_checks >= 3 )) && exit 0
        last_signature=""
        /bin/sleep "$POLL_INTERVAL_SECONDS"
        continue
    fi
    missing_btt_checks=0

    music_pid="$(/usr/bin/pgrep -x Music 2>/dev/null | tr '\n' ',')"
    remote_state=""
    if command -v nowplaying-cli >/dev/null 2>&1; then
        remote_state="$(nowplaying-cli get --json title artist clientBundleIdentifier playbackRate 2>/dev/null || true)"
    fi
    signature="$music_pid|$remote_state"

    if [[ "$signature" != "$last_signature" ]]; then
        /usr/bin/osascript -l JavaScript - "$LYRICS_UUID" "$STAR_UUID" <<'JXA' >/dev/null 2>&1
function run(argv) {
    const btt = Application("BetterTouchTool");
    for (const uuid of argv) {
        btt.refresh_widget(uuid);
    }
}
JXA
        last_signature="$signature"
    fi

    /bin/sleep "$POLL_INTERVAL_SECONDS"
done
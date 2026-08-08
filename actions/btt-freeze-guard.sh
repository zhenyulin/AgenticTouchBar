#!/usr/bin/env zsh
#
# Periodic BetterTouchTool restart and widget refresh.
#
# Root cause, confirmed by live capture rather than just code reading (see
# widgets/test-widget.sh and actions/freeze-catch.sh): a `sample` taken while
# frozen (~/Library/Caches/btt-widgets/freeze-samples/20260808-180758/)
# caught BetterTouchTool's own main thread 100% busy inside AppKit --
# -[NSWindow recalculateKeyViewLoop] recursing dozens of frames deep through
# NSPerformVisuallyAtomicChange/_layoutSubtreeWithOldSize: while decoding a
# NIB. That blocks BTT's run loop, which is also what dispatches widget ticks
# to its BetterTouchToolShellScriptRunner XPC helper -- so nothing runs,
# system-wide, until that layout pass finishes. test-widget.sh froze the same
# way with zero network or Apple Music work involved, which rules out any
# particular widget's own logic (including the historical lyrics nohup/setsid
# gap noted in actions/track-changed.sh -- a real
# bug, but not this one: it can only wedge the lyrics widget's own tick, not
# every other widget at once).
#
# The actual fix is on BTT's side (an AppKit performance bug in its own
# code); there is nothing in this repo to patch. Until upstream fixes it,
# this script restarts BTT on every 3-minute LaunchAgent interval and then
# explicitly refreshes every script widget. A fixed interval is intentional:
# a widget-specific stall or a partially frozen BTT is not reliably visible
# through the shared trace file.
#
# This file is the documented, version-controlled source. The LaunchAgent
# does not run it from here: launchd spawns its own zsh under a TCC identity
# that macOS blocks from ~/Documents (same restriction noted in
# widgets/lib/btt-widget.sh re: btt_refresh_detached), even though BTT
# itself can read this whole repo fine. The actually-running copy lives at
# ~/Library/Application Support/BTT/btt-freeze-guard.sh -- re-copy this file
# there after making changes.
#

set -u

LOG="$HOME/Library/Caches/btt-widgets/freeze-guard.log"

mkdir -p "$(dirname "$LOG")" 2>/dev/null

echo "$(date '+%Y-%m-%d %H:%M:%S') scheduled restart -- restarting BTT" >> "$LOG"

/usr/bin/osascript -e 'tell application "BetterTouchTool" to quit' >/dev/null 2>&1
sleep 3
# Belt and suspenders: the quit above can itself be a no-op if BTT is deep
# enough into the wedge, so make sure it's actually gone before reopening.
killall -9 BetterTouchTool BTTRelaunch >/dev/null 2>&1
sleep 1
/usr/bin/open -a "BetterTouchTool"
sleep 5

/usr/bin/osascript <<'APPLESCRIPT' >/dev/null 2>&1
tell application "BetterTouchTool"
	refresh_widget "59F8C568-022F-4BD9-B3EB-63A7676592DF"
	refresh_widget "CF76E4C0-5986-41F9-8F3E-00A6C8F160FE"
	refresh_widget "E19BB023-5060-4A56-95C8-6E7402779870"
	refresh_widget "8C95B746-77DA-4B76-A966-6EBB10E755F4"
	refresh_widget "508ECCA7-BAD5-469D-9418-94E2C370AE37"
	refresh_widget "E25C395A-FE13-4216-BC59-6317FD0454BF"
end tell
APPLESCRIPT

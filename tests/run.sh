#!/usr/bin/env zsh
#
# Run the test suite.
#
#   tests/run.sh                    # everything
#   tests/run.sh lyrics.test_lrc    # one module
#
# Plain stdlib unittest, no pytest: the widgets themselves run under whatever
# python3 BetterTouchTool's PATH finds, with no site-packages, so the tests
# deliberately need nothing installed either.
#
# Every path lyrics.config exposes is redirected into a temporary directory
# before Python starts, so a run never touches cache/lyrics or logs/.
#

set -u

REPO_DIR="${BTT_REPO_DIR:-${0:A:h:h}}"
SANDBOX="$(mktemp -d -t btt-lyrics-tests)"
trap 'rm -rf "$SANDBOX"' EXIT

export PYTHONPATH="$REPO_DIR/widgets:$REPO_DIR${PYTHONPATH:+:$PYTHONPATH}"
export BTT_REPO_DIR="$REPO_DIR"
export BTT_LYRICS_CACHE_DIR="$SANDBOX/cache"
export BTT_LOG_DIR="$SANDBOX/logs"
export BTT_LYRICS_LOCAL_DIR="$SANDBOX/local-lrc"
# The widget-facing cache and log directories, which the shell widgets derive
# from BTT_WIDGET_CACHE_DIR and BTT_WIDGET_LOG_DIR. Without these the run
# reads and writes the checkout's own cache/, so it depends on that directory
# existing -- which it does on a machine that has run the widgets, and does
# not in a fresh clone, where two transition tests then fail on a marker they
# cannot write.
export BTT_WIDGET_CACHE_DIR="$SANDBOX/cache"
export BTT_WIDGET_LOG_DIR="$SANDBOX/logs"
export BTT_LYRICS_TRACE=0
mkdir -p "$BTT_LYRICS_CACHE_DIR" "$BTT_LOG_DIR" "$BTT_LYRICS_LOCAL_DIR"

if (( $# )); then
    exec python3 -m unittest -v "tests.$1"
fi

exec python3 -m unittest discover -s "$REPO_DIR/tests" -t "$REPO_DIR" -v

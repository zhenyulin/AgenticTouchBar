#!/usr/bin/env zsh
#
# First-run preflight for this repository.
#
# Usage:
#   actions/doctor.sh
#
# Every widget here fails soft: a missing dependency prints a short label in
# the Touch Bar and the trace records why. That is the right behaviour on a
# running bar, but it is a poor way to find out what a fresh checkout still
# needs -- the failure shows up as a widget that quietly says "NO CODEXBAR",
# three layers away from the cause. This script asks the same questions in
# one pass and prints the fix beside each answer.
#
# Exit status is 0 when nothing required is missing, 1 when something is.
# Warnings never affect the exit status: they cover optional integrations
# (codexbar, Clash, Apple Music) that a given machine may simply not have.
#
# Nothing here writes to BTT: the AppleScript probes it makes below are
# read-only, so it is safe to run at any time.
#

set -u

PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# ${commands[...]} is zsh's PATH hash: a lookup, where `command -v` in a
# command substitution would be a fork.
zmodload zsh/parameter 2>/dev/null || true

# Unlike the widget scripts -- which BTT invokes with no context and which must
# therefore fall back to ~/Documents/BTT -- this one is run by hand from a
# checkout, so it reports on the checkout it lives in. That is what makes the
# clone-location check further down mean anything.
REPO_DIR="${BTT_REPO_DIR:-${0:A:h:h}}"
CACHE_DIR="${BTT_WIDGET_CACHE_DIR:-$REPO_DIR/cache}"
LOG_DIR="${BTT_LOG_DIR:-$REPO_DIR/logs}"
STATE_BIN="${BTT_NOW_PLAYING_STATE_BIN:-$HOME/Library/Application Support/BTT/nowplaying-state}"
CLASH_SOCKET="${CLASH_SOCKET:-/tmp/verge/verge-mihomo.sock}"

FAILED=0
WARNED=0

ok() {
    printf '%-22s ok' "$1"
    [[ -n "${2:-}" ]] && printf '   %s' "$2"
    printf '\n'
    return 0
}

warn() {
    printf '%-22s warn' "$1"
    [[ -n "${2:-}" ]] && printf '   %s' "$2"
    printf '\n'
    if [[ -n "${3:-}" ]]; then
        printf '%-22s      %s\n' '' "$3"
    fi
    (( WARNED++ ))
    return 0
}

fail() {
    printf '%-22s FAIL' "$1"
    [[ -n "${2:-}" ]] && printf '   %s' "$2"
    printf '\n'
    if [[ -n "${3:-}" ]]; then
        printf '%-22s      %s\n' '' "$3"
    fi
    (( FAILED++ ))
    return 0
}

section() {
    printf '\n%s\n' "$1"
    return 0
}

# A command on PATH, reported as required or optional.
check_command() {
    local name="$1" label="$2" required="$3" hint="$4"
    local path="${commands[$name]:-}"
    if [[ -n "$path" ]]; then
        ok "$label" "$path"
    elif [[ "$required" == "yes" ]]; then
        fail "$label" 'not found' "$hint"
    else
        warn "$label" 'not found' "$hint"
    fi
}


section 'System'

if [[ "$(uname -s)" == 'Darwin' ]]; then
    ok 'macOS' "$(sw_vers -productVersion 2>/dev/null)"
else
    fail 'macOS' "running $(uname -s)" 'these widgets are macOS-only'
fi

# TouchBarServer only runs on a Mac with a physical Touch Bar. Its absence is
# not an error: BTT drives the Control Strip on every other Mac, and that is
# what the preset targets when no Touch Bar is present.
if pgrep -x TouchBarServer >/dev/null 2>&1; then
    ok 'Touch Bar' 'physical hardware present'
else
    warn 'Touch Bar' 'no TouchBarServer process' \
        'expected on a Touch Bar Mac; other Macs need BTT Control Strip mode'
fi

section 'Dependencies'

# The lyrics package uses `from __future__ import annotations` and no match
# statements, so macOS's system python3 (3.9) is enough -- there is nothing
# to install.
if [[ -z "${commands[python3]:-}" ]]; then
    fail 'python3' 'not found' 'install the Xcode Command Line Tools: xcode-select --install'
elif python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    ok 'python3' "$(python3 -c 'import platform; print(platform.python_version())' 2>/dev/null)"
else
    fail 'python3' "found $(python3 -c 'import platform; print(platform.python_version())' 2>/dev/null), need 3.9+" \
        'install the Xcode Command Line Tools: xcode-select --install'
fi

check_command jq 'jq' yes 'brew install jq'
check_command curl 'curl' yes 'ships with macOS; check /usr/bin'
check_command nowplaying-cli 'nowplaying-cli' yes 'brew install nowplaying-cli'

# codexbar feeds the Claude and Codex quota widgets; the OpenCode one reads
# its plan's usage API instead. A machine without it still runs everything
# else, so its absence is a warning rather than a failure.
check_command codexbar 'codexbar' no 'brew install codexbar (quota widgets only)'

section 'MediaRemote helper'

# This helper is the only source of the isPlaying flag, and it cannot be
# installed from a package manager -- it is compiled from actions/
# nowplaying-state.m against a private framework. Without it the Now Playing
# widget still shows the track, but keeps the album cover where the play
# icon belongs while paused, which is exactly the bug the helper exists to
# fix. Its absence is therefore a hard failure with the build line attached.
if [[ ! -e "$STATE_BIN" ]]; then
    fail 'nowplaying-state' 'not built' \
        "clang -O2 $REPO_DIR/actions/nowplaying-state.m -framework Foundation -F/System/Library/PrivateFrameworks -framework MediaRemote -o '$STATE_BIN'"
elif [[ ! -x "$STATE_BIN" ]]; then
    fail 'nowplaying-state' 'not executable' "chmod +x '$STATE_BIN'"
else
    # The binary bounds its own MediaRemote read, so running it here cannot
    # hang. It answers {"isPlaying": false} when no app holds a session.
    state_output="$("$STATE_BIN" 2>/dev/null)"
    if [[ "$state_output" == *'"isPlaying"'* ]]; then
        ok 'nowplaying-state' 'built and answering'
    else
        fail 'nowplaying-state' 'built but returned no isPlaying flag' \
            "re-check the build, then run it by hand: '$STATE_BIN'"
    fi
    unset state_output
fi

section 'Repository'

if [[ ! -d "$REPO_DIR" ]]; then
    fail 'repo directory' "does not exist: $REPO_DIR" \
        'set BTT_REPO_DIR to the checkout, or clone to ~/Documents/BTT'
elif [[ ! -w "$REPO_DIR" ]]; then
    fail 'repo directory' "not writable: $REPO_DIR" \
        'cache/ and logs/ live here; fix ownership or set BTT_REPO_DIR'
elif [[ ! -x "$REPO_DIR/actions/doctor.sh" ]]; then
    fail 'repo directory' "not this repository: $REPO_DIR" \
        'BTT_REPO_DIR must point at the checkout that holds actions/ and widgets/'
else
    ok 'repo directory' "$REPO_DIR"
fi

# The cache and log directories are created on demand by the widgets, so the
# check is whether they *can* be created, not whether they exist yet.
if mkdir -p "$CACHE_DIR" 2>/dev/null && touch "$CACHE_DIR/.doctor" 2>/dev/null; then
    rm -f "$CACHE_DIR/.doctor"
    ok 'cache writable' "$CACHE_DIR"
else
    fail 'cache writable' "$CACHE_DIR" 'check permissions on the repository directory'
fi

if mkdir -p "$LOG_DIR" 2>/dev/null && touch "$LOG_DIR/.doctor" 2>/dev/null; then
    rm -f "$LOG_DIR/.doctor"
    ok 'logs writable' "$LOG_DIR"
else
    fail 'logs writable' "$LOG_DIR" 'check permissions on the repository directory'
fi

# The exported preset invokes every script through "$HOME/Documents/BTT/...",
# so a clone elsewhere works for the scripts but not for the preset. Worth
# saying out loud, because the symptom is a tap that does nothing at all.
if [[ "$REPO_DIR" == "$HOME/Documents/BTT" ]]; then
    ok 'clone location' "$REPO_DIR"
else
    warn 'clone location' "$REPO_DIR" \
        'the preset hard-codes $HOME/Documents/BTT; rewrite its script paths or re-export'
fi

if [[ -f "$REPO_DIR/.env" ]]; then
    ok '.env' 'present'
elif [[ -n "${BTT_WEATHER_QW_KEY:-}" ]]; then
    ok 'QWeather' 'configured through the environment'
else
    warn 'QWeather' 'no key configured' \
        'optional: cp .env-template .env and add a key from console.qweather.com'
fi

section 'BetterTouchTool'

if [[ -d /Applications/BetterTouchTool.app ]]; then
    ok 'BTT installed' '/Applications/BetterTouchTool.app'
else
    fail 'BTT installed' 'not found' 'install from https://folytools.io or the Mac App Store'
fi

if pgrep -x BetterTouchTool >/dev/null 2>&1; then
    ok 'BTT running' "pid $(pgrep -x BetterTouchTool | head -1)"
else
    fail 'BTT running' 'not running' 'launch BetterTouchTool, then re-run this script'
fi

# The probe is a read-only command taken from the preset's own AppleScript, so
# a success here means Automation permission is genuinely granted for BTT.
# Its stderr distinguishes the three ways this can fail: permission denied
# (-1743), BTT not answering (-600/-609), and anything else.
probe_error="$(osascript -e 'tell application "BetterTouchTool" to get_active_touch_bar_group' 2>&1 >/dev/null)"
probe_status=$?
if (( probe_status == 0 )); then
    ok 'Automation' 'BTT is scriptable'
elif [[ "$probe_error" == *'-1743'* || "$probe_error" == *'not allowed'* || "$probe_error" == *'Not authorized'* ]]; then
    fail 'Automation' 'permission denied for BTT' \
        'System Settings > Privacy & Security > Automation > enable BetterTouchTool'
elif [[ "$probe_error" == *'-600'* || "$probe_error" == *'-609'* ]]; then
    fail 'Automation' 'BTT is not answering AppleScript' \
        'launch BetterTouchTool, then re-run this script'
else
    warn 'Automation' 'probe failed for an unexpected reason' "$probe_error"
fi

section 'Optional integrations'

# The Clash widgets read the controller over its own socket or HTTP API. Both
# are optional: without a controller they print "Controller" and stop.
if [[ -S "$CLASH_SOCKET" || -e "$CLASH_SOCKET" ]]; then
    ok 'Clash socket' "$CLASH_SOCKET"
else
    warn 'Clash socket' "not found: $CLASH_SOCKET" \
        'Clash/Mihomo widgets only; set CLASH_SOCKET or CLASH_API for another controller'
fi

# The weather widgets ask BTT where the Mac is instead of carrying a query
# point of their own, so a machine that has not granted BTT Location Services
# has no point at all: QWeather, Open-Meteo and get_weather are skipped and no
# source is left (the Shortcut bridge, which needed no point, was parked
# 2026-09-18). Nothing on the bar shows that -- a row that keeps its last
# reading looks like a working one -- so it is worth catching here, and the
# widget answers the same question the same way.
if weather_location="$("$REPO_DIR/widgets/weather.sh" --location 2>/dev/null)" \
        && [[ -n "$weather_location" ]]; then
    ok 'weather location' "$weather_location from BTT"
else
    warn 'weather location' 'BTT reports no location' \
        'System Settings > Privacy & Security > Location Services > BetterTouchTool, or the weather sources that need a point stay off'
fi

# Apple Music is what the Lyrics and Star widgets read directly. Other
# players still drive the Now Playing row through MediaRemote.
if [[ -d /System/Applications/Music.app ]]; then
    ok 'Apple Music' 'installed'
else
    warn 'Apple Music' 'not installed' \
        'Lyrics and Star need it; the Now Playing row works with any allowed player'
fi

section 'Summary'

if (( FAILED == 0 && WARNED == 0 )); then
    printf 'All checks passed.\n\n'
    printf 'Next: import bttpreset/Default.bttpreset, then run\n'
    printf '  actions/set-widget-variables.sh\n'
elif (( FAILED == 0 )); then
    printf 'No blocking problems. %d optional item(s) above.\n\n' "$WARNED"
    printf 'Next: import bttpreset/Default.bttpreset, then run\n'
    printf '  actions/set-widget-variables.sh\n'
else
    printf '%d blocking problem(s)' "$FAILED"
    (( WARNED > 0 )) && printf ' and %d optional item(s)' "$WARNED"
    printf ' above.\n\n'
    printf 'Fix the FAIL lines, then re-run actions/doctor.sh.\n'
fi

(( FAILED == 0 )) || exit 1

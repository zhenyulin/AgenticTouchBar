#!/usr/bin/env zsh
#
# BetterTouchTool weather widgets.
#
# The BTT-native weather widgets fetch from a provider this network cannot
# reach (Clash drops it), so these render BetterTouchTool's own get_weather
# AppleScript command (Apple WeatherKit) instead, which works from here.
#
# Two instances, chosen by the first argument:
#   --text   "77°F" over "41%"      (the Weather widget)
#   --icon   an emoji for the current conditions (the Weath Icon widget)
#
# While something plays they emit nothing, and BTT hides a script widget
# whose text is empty -- the same rule that hides the Lyrics widget. The Now
# Playing play/pause action makes the flip instant: it records the state via
# actions/weather-state.sh and refreshes both widgets. Without a tap, this
# widget's own interval picks the change up from the sampler's
# cache/lyrics/state.json within its hold window.
#

set -u
set -o pipefail
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

MODE="${1:-text}"
case "$MODE" in
    --text|text) MODE="text" ;;
    --icon|icon) MODE="icon" ;;
    *) exit 2 ;;
esac

SELF="${0:A}"
source "${SELF:h}/lib/btt-widget.sh"

REPO_DIR="${BTT_REPO_DIR:-$HOME/Documents/BTT}"
LYRICS_STATE="$REPO_DIR/cache/lyrics/state.json"
# How fresh the Now Playing tap's recorded state must be to win over the
# sampler, and how old a sampler sample may be before the state is unknown.
TAP_STATE_MAX_AGE="${BTT_WEATHER_TAP_MAX_AGE:-5}"
SAMPLE_MAX_AGE="${BTT_WEATHER_SAMPLE_MAX_AGE:-8}"
# How long a get_weather result is reused before asking BTT again.
WEATHER_TTL="${BTT_WEATHER_TTL:-300}"
# get_weather always returns Celsius, so the script converts when needed.
UNIT="${BTT_WEATHER_UNIT:-celsius}"

playing_now() {
    local now st ts
    now="$(date +%s)"

    # The Now Playing tap knows the new state before the sampler does;
    # prefer its record while it is fresh.
    if btt_cache_get weather-tap-state "$TAP_STATE_MAX_AGE" >/dev/null 2>&1; then
        st="$(btt_cache_get weather-tap-state 999999999 2>/dev/null || true)"
        [[ "$st" == "playing" ]] && return 0
        [[ "$st" == "paused" ]] && return 1
    fi

    # Otherwise follow the sampler, mirroring the Lyrics widget's rule.
    if [[ -f "$LYRICS_STATE" ]]; then
        st="$(jq -r '.track.state // ""' "$LYRICS_STATE" 2>/dev/null)"
        ts="$(jq -r '.sampled_at // 0' "$LYRICS_STATE" 2>/dev/null)"
        ts="${ts%.*}"
        if [[ "$ts" =~ ^[0-9]+$ ]] && (( now - ts <= SAMPLE_MAX_AGE )); then
            [[ "$st" == "playing" ]] && return 0
        fi
    fi

    return 1
}

weather_json() {
    # A fresh cache wins; otherwise ask BTT (Apple WeatherKit) and cache it.
    if btt_cache_get weather.data "$WEATHER_TTL" >/dev/null 2>&1; then
        btt_cache_get weather.data 999999999 2>/dev/null
        return 0
    fi

    local json
    json="$(osascript -e 'tell application "BetterTouchTool" to get_weather' 2>/dev/null)"
    if [[ -n "$json" ]] && jq -e . >/dev/null 2>&1 <<<"$json"; then
        btt_cache_put weather.data "$json"
        print -r -- "$json"
        return 0
    fi
    return 1
}

if playing_now; then
    # Empty text: BTT hides the widget.
    btt_publish ""
    exit 0
fi

if ! json="$(weather_json)"; then
    if [[ "$MODE" == icon ]]; then
        btt_publish ""
    else
        btt_publish "--"
    fi
    exit 0
fi

if [[ "$MODE" == icon ]]; then
    icon="$(jq -r '.currently.icon // ""' <<<"$json")"
    case "$icon" in
        clear-day)          emoji="☀️" ;;
        clear-night)        emoji="🌙" ;;
        partly-cloudy-day)  emoji="⛅" ;;
        partly-cloudy-night) emoji="☁️" ;;
        cloudy)             emoji="☁️" ;;
        rain)               emoji="🌧️" ;;
        sleet)              emoji="🌨️" ;;
        snow)               emoji="❄️" ;;
        wind)               emoji="💨" ;;
        fog)                emoji="🌫️" ;;
        thunderstorm)       emoji="⛈️" ;;
        hail)               emoji="🌨️" ;;
        *)                  emoji="🌡️" ;;
    esac
    btt_publish "$emoji"
    exit 0
fi

temp="$(jq -r '.currently.temperature // 0' <<<"$json")"
humidity="$(jq -r '.currently.humidity // 0' <<<"$json")"
if [[ "$UNIT" == fahrenheit ]]; then
    temp="$(awk -v t="$temp" 'BEGIN{printf "%.0f", t * 9 / 5 + 32}')"
    suffix="F"
else
    temp="$(awk -v t="$temp" 'BEGIN{printf "%.0f", t}')"
    suffix="C"
fi
humidity="$(awk -v h="$humidity" 'BEGIN{printf "%.0f", h * 100}')"

btt_publish "${temp}°${suffix}"$'\n'"${humidity}%"

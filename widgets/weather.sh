#!/usr/bin/env zsh
#
# BetterTouchTool weather widgets.
#
# The BTT-native weather widgets fetch from a provider this network cannot
# reach (Clash drops it), so these render BetterTouchTool's own get_weather
# AppleScript command (Apple WeatherKit) instead, which works from here.
#
# Two instances, chosen by the mode flag:
#   --text   "77°F" over "41%"      (the Weather widget)
#   --icon   an emoji for the current conditions (the Weath Icon widget)
#   --refresh   re-ask BTT (Apple WeatherKit) and refresh the cache; runs
#               detached, never in the widget path
#
# A tap (actions/tap-refresh.sh) drops a force flag and re-runs the widget:
# the widget starts a detached refresh even while its cache is fresh, and
# paints itself grey while the refresh lock is held. The redraw that ends
# the refresh restores the normal color with the new value -- the same
# dim-while-working frame the Clash widgets use. Both instances refresh
# together, since they share one weather.data cache.
#
# The widgets render only from cache/weather.data.value: a fresh value wins,
# and a stale one still prints, so when a song ends they come back with the
# previous values instantly while a detached refresh re-asks BTT.
#
# While something plays they emit nothing, and BTT hides a script widget
# whose text is empty -- the same rule that hides the Lyrics widget. The Now
# Playing play/pause action makes the flip instant: it records the state via
# actions/weather-state.sh and refreshes both widgets. Without a tap, this
# widget's own interval picks the change up from the sampler's
# cache/lyrics/state.json within its hold window. The hide is disabled for
# now (HIDE_WHILE_PLAYING=0): the widgets always render, and setting the
# flag to 1 gives the slot back to the pair while something plays.
#

set -u
set -o pipefail
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# Flags in any order: --refresh, the mode (--text/--icon), and the widget
# UUID the preset passes as a positional (like the Clash widgets).
REFRESH_MODE=0
MODE="text"
UUID_CANDIDATE=""
for arg in "$@"; do
    case "$arg" in
        --refresh)   REFRESH_MODE=1 ;;
        --text|text) MODE="text" ;;
        --icon|icon) MODE="icon" ;;
        --*)         exit 2 ;;
        *)           UUID_CANDIDATE="$arg" ;;
    esac
done
BTT_WIDGET_UUID="${UUID_CANDIDATE:-${BTT_WIDGET_UUID:-}}"

SELF="${0:A}"
source "${SELF:h}/lib/btt-widget.sh"

STARTED="$(btt_now)"
BTT_WIDGET_NAME="weather"
# A refresh is one get_weather call; a lock older than this is dead.
BTT_WIDGET_REFRESH_MAX_RUN=60

REPO_DIR="${BTT_REPO_DIR:-$HOME/Documents/BTT}"
LYRICS_STATE="$REPO_DIR/cache/lyrics/state.json"
# How fresh the Now Playing tap's recorded state must be to win over the
# sampler, and how old a sampler sample may be before the state is unknown.
TAP_STATE_MAX_AGE="${BTT_WEATHER_TAP_MAX_AGE:-5}"
SAMPLE_MAX_AGE="${BTT_WEATHER_SAMPLE_MAX_AGE:-8}"
# The weather widgets used to hide while something plays, handing their
# slot to the Now Playing + Lyrics pair. Disabled for now: they always
# render; set to 1 to re-enable the hide.
HIDE_WHILE_PLAYING=0
# How long a get_weather result is reused before asking BTT again.
WEATHER_TTL="${BTT_WEATHER_TTL:-300}"
# get_weather always returns Celsius, so the script converts when needed.
UNIT="${BTT_WEATHER_UNIT:-celsius}"

if (( REFRESH_MODE )); then
    # osascript auto-launches a dead BTT; only query it while it is running.
    /usr/bin/pgrep -x BetterTouchTool >/dev/null 2>&1 || { btt_trace refresh "$STARTED" error "no-btt"; exit 1; }
    json="$(osascript -e 'tell application "BetterTouchTool" to get_weather' 2>/dev/null)"
    if [[ -n "$json" ]] && jq -e . >/dev/null 2>&1 <<<"$json"; then
        btt_cache_put weather.data "$json"
        btt_trace refresh "$STARTED" ok "value=$json"
        exit 0
    fi
    btt_trace refresh "$STARTED" error "bad-json"
    exit 1
fi

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

if (( HIDE_WHILE_PLAYING )) && playing_now; then
    # Empty text: BTT hides the widget.
    exit 0
fi

# Render only from the cache: a fresh value wins, and a stale one still
# prints, so a reveal after a long playback republishes the previous values
# instantly, while a detached refresh re-asks BTT (Apple WeatherKit) -- the
# widget path never blocks on AppleScript. A tap's force flag refreshes
# even while the value is fresh: the widget greys out while the refresh
# lock is held, and the redraw that ends the refresh restores white with
# the new value.
VALUE="$(btt_cache_get weather.data "$WEATHER_TTL")"
FRESH=$?
FORCE=0; btt_force_pending && FORCE=1
if (( FRESH != 0 || FORCE )); then
    btt_refresh_detached weather "$BTT_WIDGET_REFRESH_MAX_RUN" "$SELF" --refresh "$BTT_WIDGET_UUID"
fi
OUTCOME=cached; (( FRESH != 0 )) && OUTCOME=stale; (( FORCE )) && OUTCOME=forced; [[ -n "$VALUE" ]] || OUTCOME=empty
btt_trace widget "$STARTED" "$OUTCOME"

if [[ -z "$VALUE" ]]; then
    if [[ "$MODE" == icon ]]; then
        exit 0
    else
        btt_publish "--"
    fi
    exit 0
fi

if [[ "$MODE" == icon ]]; then
    icon="$(jq -r '.currently.icon // ""' <<<"$VALUE")"
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

temp="$(jq -r '.currently.temperature // 0' <<<"$VALUE")"
humidity="$(jq -r '.currently.humidity // 0' <<<"$VALUE")"
if [[ "$UNIT" == fahrenheit ]]; then
    temp="$(awk -v t="$temp" 'BEGIN{printf "%.0f", t * 9 / 5 + 32}')"
    suffix="F"
else
    temp="$(awk -v t="$temp" 'BEGIN{printf "%.0f", t}')"
    suffix="C"
fi
humidity="$(awk -v h="$humidity" 'BEGIN{printf "%.0f", h * 100}')"

btt_publish "${temp}°${suffix}"$'\n'"${humidity}%"

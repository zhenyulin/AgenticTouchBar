#!/usr/bin/env zsh
#
# BetterTouchTool weather widgets.
#
# The BTT-native weather widgets fetch from a provider this network cannot
# reach (Clash drops it), so these widgets fetch conditions themselves: the
# refresh asks Open-Meteo first (no key, fast, reachable from here) and
# falls back to BetterTouchTool's own get_weather AppleScript command
# (Apple WeatherKit) when it cannot.
#
# Two instances, chosen by the mode flag:
#   --text   "77°F" over "41%"      (the Weather widget)
#   --icon   an emoji for the current conditions (the Weath Icon widget)
#   --refresh   fetch conditions and refresh the cache; runs detached,
#               never in the widget path
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
# previous values instantly while a detached refresh fetches new ones.
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
# How long a fetched result is reused before the sources are asked again.
WEATHER_TTL="${BTT_WEATHER_TTL:-300}"
# Both sources return Celsius, so the script converts when needed.
UNIT="${BTT_WEATHER_UNIT:-celsius}"
# Where to ask for conditions; the coordinates BTT's get_weather payload
# carries (cache/weather.data.value .currently.metadata).
WEATHER_LAT="${BTT_WEATHER_LAT:-38.5}"
WEATHER_LON="${BTT_WEATHER_LON:-106.31}"

# WMO weather code -> the icon names the render path below already maps to
# emoji. is_day picks the night variant of clear/partly-cloudy.
wmo_icon() {
    local code="${1:-}" day="${2:-1}"
    case "$code" in
        0)    [[ "$day" == 1 ]] && printf 'clear-day' || printf 'clear-night' ;;
        1|2)  [[ "$day" == 1 ]] && printf 'partly-cloudy-day' || printf 'partly-cloudy-night' ;;
        3)    printf 'cloudy' ;;
        45|48) printf 'fog' ;;
        51|53|55|61|63|65|80|81|82) printf 'rain' ;;
        56|57|66|67) printf 'sleet' ;;
        71|73|75|77|85|86) printf 'snow' ;;
        95)   printf 'thunderstorm' ;;
        96|99) printf 'hail' ;;
        *)    printf 'cloudy' ;;
    esac
}

# Open-Meteo current conditions. Returns the same {"currently":
# {temperature, humidity, icon}} shape BTT's payload had (humidity as a
# 0-1 fraction), so the render path stays unchanged.
fetch_open_meteo() {
    local payload temp humidity code day
    payload="$(curl -fsS --connect-timeout 2 --max-time 5 \
        "https://api.open-meteo.com/v1/forecast?latitude=$WEATHER_LAT&longitude=$WEATHER_LON&current=temperature_2m,relative_humidity_2m,weather_code,is_day" 2>/dev/null)" || return 1
    temp="$(jq -r '.current.temperature_2m' <<<"$payload" 2>/dev/null)"
    humidity="$(jq -r '.current.relative_humidity_2m' <<<"$payload" 2>/dev/null)"
    code="$(jq -r '.current.weather_code' <<<"$payload" 2>/dev/null)"
    day="$(jq -r '.current.is_day' <<<"$payload" 2>/dev/null)"
    [[ "$temp" =~ ^-?[0-9]+(\.[0-9]+)?$ ]] || return 1
    [[ "$humidity" =~ ^[0-9]+(\.[0-9]+)?$ ]] || return 1
    [[ "$code" =~ ^[0-9]+$ ]] || return 1
    jq -cn --argjson t "$temp" --argjson h "$humidity" --arg icon "$(wmo_icon "$code" "$day")" \
        '{currently: {temperature: $t, humidity: ($h / 100), icon: $icon}}'
}

# Fallback: BTT's get_weather (Apple WeatherKit). Bounded, because a wedged
# BTT can make the call hang for minutes; osascript auto-launches a dead
# BTT, so only query it while it is running.
fetch_btt_weather() {
    /usr/bin/pgrep -x BetterTouchTool >/dev/null 2>&1 || return 1
    local tmp pid i
    tmp="$(mktemp)" || return 1
    (
        /usr/bin/osascript -e 'tell application "BetterTouchTool" to get_weather' >"$tmp" 2>/dev/null &
        pid=$!
        for i in {1..24}; do
            kill -0 "$pid" 2>/dev/null || break
            /bin/sleep 0.5
        done
        kill "$pid" 2>/dev/null
        wait "$pid" 2>/dev/null
    )
    cat "$tmp"
    rm -f "$tmp"
}

if (( REFRESH_MODE )); then
    json="$(fetch_open_meteo)"
    SOURCE=open-meteo
    if [[ -z "$json" ]]; then
        json="$(fetch_btt_weather)"
        SOURCE=btt
    fi
    if [[ -n "$json" ]] && jq -e . >/dev/null 2>&1 <<<"$json"; then
        btt_cache_put weather.data "$json"
        summary="$(jq -r '"t=" + (.currently.temperature|tostring) + " h=" + ((.currently.humidity * 100)|round|tostring)' <<<"$json" 2>/dev/null || true)"
        btt_trace refresh "$STARTED" ok "source=$SOURCE $summary"
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
# instantly, while a detached refresh re-fetches conditions (Open-Meteo, or
# BTT's Apple WeatherKit as a fallback) -- the widget path never blocks on
# AppleScript or the network. A tap's force flag refreshes even while the
# value is fresh: the widget greys out while the refresh lock is held, and
# the redraw that ends the refresh restores white with the new value.
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

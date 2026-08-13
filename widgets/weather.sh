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
# Both instances share one weather.data cache entry and one refresh lock
# (BTT_WIDGET_VALUE_NAME below), so a refresh started by either serves both
# and both grey out while it runs. The cache holds the conditions rather than
# a label, and BTT_WIDGET_RENDER draws the label from it -- everything else
# about the lifecycle (the force flag a tap drops, the dim-while-working
# frame, the trace) is the shared one in lib/btt-widget.sh.
#
set -u
set -o pipefail
PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# Flags in any order: --refresh, the mode (--text/--icon), and the widget
# UUID the preset passes as a positional (like the Clash widgets). Parsed
# here rather than by btt_parse_widget_args, which knows only the first two.
REFRESH_MODE=0
MODE="text"
UUID_CANDIDATES=()
for arg in "$@"; do
    case "$arg" in
        --refresh)   REFRESH_MODE=1 ;;
        --text|text) MODE="text" ;;
        --icon|icon) MODE="icon" ;;
        --*)         exit 2 ;;
        *)           UUID_CANDIDATES+=("$arg") ;;
    esac
done
# The preset passes both weather widget UUIDs, own first. Siblings go to
# BTT_WIDGET_REDRAW_UUIDS: both instances paint the dim frame while the
# shared weather refresh lock is held, and the refresh wrapper must repaint
# both when it drops the lock, or the non-spawning one stays grey.
BTT_WIDGET_UUID="${UUID_CANDIDATES[1]:-${BTT_WIDGET_UUID:-}}"
for (( i = 2; i <= ${#UUID_CANDIDATES[@]}; i++ )); do
    BTT_WIDGET_REDRAW_UUIDS+="${BTT_WIDGET_REDRAW_UUIDS:+ }${UUID_CANDIDATES[i]}"
done

SELF="${0:A}"
source "${SELF:h}/lib/btt-widget.sh"

BTT_WIDGET_NAME="weather"
# One entry, drawn two ways.
BTT_WIDGET_VALUE_NAME="weather.data"
# A refresh is one get_weather call; a lock older than this is dead.
BTT_WIDGET_REFRESH_MAX_RUN=60
BTT_WIDGET_RENDER=render_conditions
BTT_WIDGET_REFRESH_DETAIL=describe_conditions

# Apple Color Emoji glyphs ignore the RGB of font_color, so the shared grey
# dim colour would not show on the icon instance's emoji at all. They do
# still honour the alpha channel, so the icon instance's dim frame fades the
# glyph instead: white at ~55% over the black bar reads as a greyed emoji.
[[ "$MODE" == icon ]] && BTT_WIDGET_DIM_COLOR="255,255,255,140"

# How long a fetched result is reused before the sources are asked again.
WEATHER_TTL="${BTT_WEATHER_TTL:-300}"
# Both sources return Celsius, so the render converts when needed.
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
# 0-1 fraction), so the render path stays unchanged. One jq run rather than
# five: it validates the fields and builds the record in the same pass.
fetch_open_meteo() {
    local payload
    payload="$(curl -fsS --connect-timeout 2 --max-time 5 \
        "https://api.open-meteo.com/v1/forecast?latitude=$WEATHER_LAT&longitude=$WEATHER_LON&current=temperature_2m,relative_humidity_2m,weather_code,is_day" 2>/dev/null)" || return 1

    local fields
    fields="$(jq -r '
        .current
        | select(
            (.temperature_2m | type) == "number"
            and (.relative_humidity_2m | type) == "number"
            and (.weather_code | type) == "number"
          )
        | "\(.temperature_2m)\t\(.relative_humidity_2m)\t\(.weather_code)\t\(.is_day // 1)"
    ' <<<"$payload" 2>/dev/null)" || return 1
    [[ -n "$fields" ]] || return 1

    local temp humidity code day
    IFS=$'\t' read -r temp humidity code day <<<"$fields"

    jq -cn --argjson t "$temp" --argjson h "$humidity" \
        --arg icon "$(wmo_icon "${code%%.*}" "${day%%.*}")" \
        '{currently: {temperature: $t, humidity: ($h / 100), icon: $icon}, source: "open-meteo"}'
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
    jq -c '. + {source: "btt"}' <"$tmp" 2>/dev/null
    rm -f "$tmp"
}

compute_value() {
    local json
    json="$(fetch_open_meteo)"
    [[ -n "$json" ]] || json="$(fetch_btt_weather)"
    # Only a payload the render path can read is worth caching; anything else
    # leaves the previous conditions in place for the next tick to draw.
    [[ -n "$json" ]] && jq -e . >/dev/null 2>&1 <<<"$json" && printf '%s' "$json"
}

# What the refresh writes into the trace, in place of the whole document.
describe_conditions() {
    jq -r '"source=" + (.source // "?")
        + " t=" + (.currently.temperature | tostring)
        + " h=" + ((.currently.humidity * 100) | round | tostring)' \
        <<<"${1-}" 2>/dev/null || printf 'source=?'
}

# The cached conditions, drawn as this instance's label. One jq run: the
# render path used to spend two on jq and two more on awk.
render_conditions() {
    local value="${1-}"

    if [[ -z "$value" ]]; then
        # An icon widget with nothing to draw prints nothing, which is how BTT
        # hides it; the text widget keeps a placeholder so the row holds shape.
        [[ "$MODE" == icon ]] || printf -- '--'
        return 0
    fi

    if [[ "$MODE" == icon ]]; then
        local icon
        icon="$(jq -r '.currently.icon // ""' <<<"$value" 2>/dev/null)"
        case "$icon" in
            clear-day)          printf '☀️' ;;
            clear-night)        printf '🌙' ;;
            partly-cloudy-day)  printf '⛅' ;;
            partly-cloudy-night) printf '☁️' ;;
            cloudy)             printf '☁️' ;;
            rain)               printf '🌧️' ;;
            sleet)              printf '🌨️' ;;
            snow)               printf '❄️' ;;
            wind)               printf '💨' ;;
            fog)                printf '🌫️' ;;
            thunderstorm)       printf '⛈️' ;;
            hail)               printf '🌨️' ;;
            *)                  printf '🌡️' ;;
        esac
        return 0
    fi

    jq -r --arg unit "$UNIT" '
        (.currently.temperature // 0) as $c
        | (if $unit == "fahrenheit" then $c * 9 / 5 + 32 else $c end) as $t
        | (if $unit == "fahrenheit" then "F" else "C" end) as $suffix
        | "\($t | round)°\($suffix)\n\(((.currently.humidity // 0) * 100) | round)%"
    ' <<<"$value" 2>/dev/null
}

btt_cached_widget_main "$REFRESH_MODE" "$SELF" "$WEATHER_TTL" compute_value

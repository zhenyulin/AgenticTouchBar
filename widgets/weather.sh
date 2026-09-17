#!/usr/bin/env zsh
#
# BetterTouchTool weather widgets.
#
# The refresh asks QWeather (和风天气) first -- the reliable domestic route when
# Clash's VPN node has timed out. Open-Meteo and BTT's own get_weather
# AppleScript command (Apple WeatherKit) remain later fallbacks.
#
# Every source is asked for a point, and the point is wherever the Mac is:
# BTT reports the machine's location, nothing here is configured or guessed.
# If BTT has no live fix, the sources are skipped rather than asked about a
# city nobody chose.
#
# An Apple Weather Shortcut bridge used to lead this ladder; it was parked on
# 2026-09-18 because a run that fails raises a Shortcuts alert, and the Mac's
# location wedge made every run fail. Git history has the implementation.
#
# Two instances, chosen by the mode flag:
#   --text   "77°F" over "41%"      (the Weather widget)
#   --icon   an emoji for the current conditions (the Weather Icon widget)
#   --refresh   fetch conditions and refresh the cache; runs detached,
#               never in the widget path
#   --location  print the query point the refresh would use, or exit 1
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
LOCATION_MODE=0
MODE="text"
UUID_CANDIDATES=()
for arg in "$@"; do
    case "$arg" in
        --refresh)   REFRESH_MODE=1 ;;
        --location)  LOCATION_MODE=1 ;;
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
# Repo-local secrets (BTT_WEATHER_QW_HOST / BTT_WEATHER_QW_KEY live here):
# plain KEY=value lines, shell-quoted if the value contains spaces.
[[ -f "$BTT_REPO_DIR/.env" ]] && source "$BTT_REPO_DIR/.env"

BTT_WIDGET_NAME="weather"
# One entry, drawn two ways.
BTT_WIDGET_VALUE_NAME="weather.data"
# A refresh is one fetch; a lock older than this is dead. The sources bound
# themselves at 12 s + 5 s + 5 s + 12 s (location, QWeather, Open-Meteo, BTT
# weather), so the lock has to outlast the 34 s worst case to stay honest.
BTT_WIDGET_REFRESH_MAX_RUN=90
BTT_WIDGET_RENDER=render_conditions
BTT_WIDGET_REFRESH_DETAIL=describe_conditions

# Apple Color Emoji glyphs ignore the RGB of font_color, so the shared grey
# dim colour would not show on the icon instance's emoji at all. They do
# still honour the alpha channel, so the icon instance's dim frame fades the
# glyph instead: white at ~55% over the black bar reads as a greyed emoji.
[[ "$MODE" == icon ]] && BTT_WIDGET_DIM_COLOR="255,255,255,140"

# How long a fetched result is reused before the sources are asked again.
WEATHER_TTL="${BTT_WEATHER_TTL:-300}"
# All sources return Celsius, so the render converts when needed.
UNIT="${BTT_WEATHER_UNIT:-celsius}"
# QWeather: domestic endpoints survive a timed-out VPN node. Both the key
# and the project's API host come from console.qweather.com (50k req/month
# free); unset either to skip QWeather and fall back to the foreign sources.
QW_KEY="${BTT_WEATHER_QW_KEY:-}"
QW_HOST="${BTT_WEATHER_QW_HOST:-}"
WEATHER_REFRESH_FAILURE_FILE="$BTT_WIDGET_CACHE_DIR/weather.refresh-failed"
BTT_WIDGET_REFRESH_BACKOFF_FILE="$WEATHER_REFRESH_FAILURE_FILE"

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

# QWeather condition code -> the icon names the render path below already
# maps to emoji. The current docs list has no night variants, but live
# responses still carry the legacy 15x night codes (150 clear, 151-153
# partly cloudy, 154 overcast).
qweather_icon() {
    local code="${1:-}"
    case "$code" in
        100)             printf 'clear-day' ;;
        150)             printf 'clear-night' ;;
        101|102|103)     printf 'partly-cloudy-day' ;;
        151|152|153)     printf 'partly-cloudy-night' ;;
        104|154)         printf 'cloudy' ;;
        302|303|304)     printf 'thunderstorm' ;;
        313)             printf 'sleet' ;;
        404|405|406)     printf 'sleet' ;;
        300|301|305|306|307|308|309|310|311|312|314|315|316|317|318|350|399) printf 'rain' ;;
        400|401|402|403|407|408|409|410|499) printf 'snow' ;;
        503|504|507|508) printf 'wind' ;;
        500|501|502|509|510|511|512|513|514|515) printf 'fog' ;;
        900)             printf 'clear-day' ;;
        901)             printf 'snow' ;;
        *)               printf 'cloudy' ;;
    esac
}

# The failure marker compute_value leaves when every source came up empty;
# the shared refresh loop reads it to back off from a cache that stays stale.
weather_mark_refresh_failed() {
    mkdir -p "$BTT_WIDGET_CACHE_DIR" 2>/dev/null &&
        : >"$WEATHER_REFRESH_FAILURE_FILE"
}

# The query point, as "lat,lon" -- the shape get_weather takes -- or
# nothing. Every point-based source below is asked about the Mac the widgets
# run on; none of them is asked about a place this repo picked.
#
# BTT is the one to ask: it hosts these widgets and is the only thing here
# that can hold a location permission. It answers a location it cannot place
# with prose ("no location available"), so the first two numbers it names are
# taken only when they are a valid pair -- a location is never read out of a
# sentence that happens to contain digits.
#
system_coordinates() {
    local raw
    raw="$(btt_osascript 'tell application "BetterTouchTool" to get_location')" || return 1

    printf '%s' "${raw//,/ }" | /usr/bin/awk '
        { for (i = 1; i <= NF; i++) if ($i ~ /^-?[0-9]+(\.[0-9]+)?$/) value[++count] = $i + 0 }
        END {
            if (count < 2) exit 1
            if (value[1] < -90 || value[1] > 90) exit 1
            if (value[2] < -180 || value[2] > 180) exit 1
            printf "%s,%s", value[1], value[2]
        }'
}

# One AppleScript answer from BTT. osascript auto-launches a BTT the user has
# quit, and a wedged BTT can make the call hang for minutes, so every call
# goes through here: skipped unless BTT is already running, abandoned after
# 12 s. Prints the answer, or nothing when there is none to print.
btt_osascript() {
    /usr/bin/pgrep -x BetterTouchTool >/dev/null 2>&1 || return 1

    local tmp pid i
    tmp="$(mktemp)" || return 1
    (
        /usr/bin/osascript -e "$1" >"$tmp" 2>/dev/null &
        pid=$!
        for i in {1..24}; do
            kill -0 "$pid" 2>/dev/null || break
            /bin/sleep 0.5
        done
        kill "$pid" 2>/dev/null
        wait "$pid" 2>/dev/null
    )

    local out
    out="$(<"$tmp")"
    rm -f "$tmp"
    [[ -n "$out" ]] || return 1
    printf '%s' "$out"
}

# The point-based providers, asked in order against one query point.
fetch_location_weather() {
    local coords
    coords="$(system_coordinates)" || return 1

    local json
    json="$(fetch_qweather "$coords")"
    [[ -n "$json" ]] || json="$(fetch_open_meteo "$coords")"
    [[ -n "$json" ]] || json="$(fetch_btt_weather "$coords")"
    [[ -n "$json" ]] && printf '%s' "$json"
}

# Open-Meteo current conditions at "lat,lon". Returns the same
# {"currently": {temperature, humidity, icon}} shape BTT's payload had
# (humidity as a 0-1 fraction), so the render path stays unchanged. One jq
# run rather than five: it validates the fields and builds the record in the
# same pass.
fetch_open_meteo() {
    local coords="${1-}"
    local lat="${coords%,*}" lon="${coords#*,}"
    local payload
    payload="$(curl -fsS --connect-timeout 2 --max-time 5 \
        "https://api.open-meteo.com/v1/forecast?latitude=$lat&longitude=$lon&current=temperature_2m,relative_humidity_2m,weather_code,is_day" 2>/dev/null)" || return 1

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

# QWeather current conditions at "lat,lon". Same shape as the other
# fetchers, one jq pass: humidity arrives as a 0-100 string, temperature as a
# string, and only a payload whose fields validate is worth handing on.
fetch_qweather() {
    [[ -n "$QW_KEY" && -n "$QW_HOST" ]] || return 1
    local coords="${1-}"
    # QWeather names the pair in its own order: longitude first.
    local lat="${coords%,*}" lon="${coords#*,}"
    local payload
    # --compressed: the API gzips every response, so plain curl gets bytes
    # that jq cannot read.
    payload="$(curl --compressed -fsS --connect-timeout 2 --max-time 5 \
        "https://${QW_HOST}/v7/weather/now?location=${lon},${lat}&key=${QW_KEY}" 2>/dev/null)" || return 1

    local fields
    fields="$(jq -r '
        select(.code == "200") | .now
        | select(
            (.temp | type) == "string" and (.temp | test("^-?[0-9.]+$"))
            and (.humidity | type) == "string" and (.humidity | test("^[0-9]+$"))
            and (.icon | type) == "string"
          )
        | "\(.temp)\t\(.humidity)\t\(.icon)"
    ' <<<"$payload" 2>/dev/null)" || return 1
    [[ -n "$fields" ]] || return 1

    local temp humidity code
    IFS=$'\t' read -r temp humidity code <<<"$fields"

    jq -cn --argjson t "$temp" --argjson h "$humidity" \
        --arg icon "$(qweather_icon "$code")" \
        '{currently: {temperature: $t, humidity: ($h / 100), icon: $icon}, source: "qweather"}'
}

# Fallback: BTT's get_weather (Apple WeatherKit) at "lat,lon". BTT answers a
# request it cannot serve with an empty document, and an empty one would reach
# the cache as a reading of 0°C, so a payload is accepted only when it carries
# the fields the render path draws.
fetch_btt_weather() {
    local coords="${1-}"
    btt_osascript "tell application \"BetterTouchTool\" to get_weather \"$coords\"" |
        jq -c '
            select(
                (.currently.temperature | type) == "number"
                and (.currently.humidity | type) == "number"
            )
            | . + {source: "btt"}
        ' 2>/dev/null
}

compute_value() {
    local json="$(fetch_location_weather)"
    # Only a payload the render path can read is worth caching; anything else
    # leaves the previous conditions in place for the next tick to draw.
    if [[ -n "$json" ]] && jq -e . >/dev/null 2>&1 <<<"$json"; then
        rm -f "$WEATHER_REFRESH_FAILURE_FILE"
        printf '%s' "$json"
    else
        weather_mark_refresh_failed
    fi
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

# The query point for a human: the weather row's "which city is this?" is
# answered here, and actions/doctor.sh asks the same way rather than growing a
# second opinion about where the Mac is.
if (( LOCATION_MODE )); then
    system_coordinates || {
        printf 'no query point: BTT reports no location (grant it Location Services)\n' >&2
        exit 1
    }
    printf '\n'
    exit 0
fi

btt_cached_widget_main "$REFRESH_MODE" "$SELF" "$WEATHER_TTL" compute_value

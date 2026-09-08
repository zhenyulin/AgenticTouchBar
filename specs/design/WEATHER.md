# Weather Widgets Specification

Indexed from [`specs/FEATURES.md`](../FEATURES.md). Caching, force flags,
dim-while-refreshing, and tracing belong to
[`specs/design/WIDGET-RUNTIME.md`](WIDGET-RUNTIME.md).

## Reconstruction Target

This document preserves what the two weather widgets show: one script in two
modes over one shared cached observation, which provider is asked first and
why, and how conditions become an emoji.

The Shortcut, QWeather, Open-Meteo, and Apple WeatherKit response schemas
beyond the fields read below are external boundaries.

## Entry Points

| Entry point | Trigger | Contract |
| --- | --- | --- |
| `widgets/weather.sh <uuid> --text` | BTT widget "Weather", 600 s interval | Two rows: temperature over relative humidity. |
| `widgets/weather.sh <uuid> --icon` | BTT widget "Weather Icon", 600 s interval | One emoji for the current conditions. |
| `widgets/weather.sh <uuid> --refresh` | The detached refresh | Fetches conditions and stores one JSON observation. Exits 1 when all sources fail. |
| Tap on either → `actions/tap-refresh.sh <weather-uuid> <icon-uuid>` | User | Both refresh together; they share one cached observation. |

Flags may appear in any order, and the widget UUID is an ordinary positional
argument. An unrecognised `--flag` exits 2.

| Variable | Default | Meaning |
| --- | --- | --- |
| `BTT_WEATHER_UNIT` | `celsius` | `fahrenheit` converts on render; all sources report Celsius. |
| `BTT_WEATHER_TTL` | `300` | How old the stored observation may be before a refresh starts. |
| `BTT_WEATHER_SHORTCUT` | `BTT Weather` | No-prompt Shortcut that returns the Apple Weather JSON contract below. |
| `BTT_WEATHER_LAT`, `BTT_WEATHER_LON` | `38.5`, `106.31` | Open-Meteo query point; taken from the coordinates BTT's own payload carried. |

## Feature Tree

```text
Weather widgets
├── One observation, two widgets
│   ├── Cache under weather.data, not under a per-widget name
│   ├── Render text or icon from the same JSON
│   └── Refresh both from either tap
├── Fetch, off the widget path
│   ├── Ask the no-prompt Shortcut first — Apple Weather
│   ├── Fall back to QWeather — domestic/direct route
│   ├── Fall back to Open-Meteo, then BTT's get_weather
│   ├── Normalise every source into one shape
│   └── Never block the widget on a provider call
├── Render
│   ├── Temperature rounded, unit applied
│   ├── Humidity as a whole percentage
│   ├── WMO code -> icon name -> emoji, with day and night variants
│   └── "--" when nothing has ever been fetched
```

## Decision Trees

### Provider Selection

| Condition | Path | Trace |
| --- | --- | --- |
| Shortcut returns valid Apple Weather JSON | Use it | `refresh ok source=shortcuts t=… h=…` |
| Shortcut is absent, times out, or returns invalid JSON | QWeather | `refresh ok source=qweather t=… h=…` |
| QWeather fails or is not configured | Open-Meteo | `refresh ok source=open-meteo t=… h=…` |
| Open-Meteo fails or returns invalid fields | BTT `get_weather` | `refresh ok source=btt t=… h=…` |
| All providers fail, or the result is not valid JSON | Nothing stored | `refresh error bad-json`, exit 1 |

The Shortcut is a supported bridge to Apple Weather rather than a read of the
Weather widget's private storage. It must not show alerts or request input,
because it runs from a detached shell refresh.

The Shortcut and `get_weather` calls are bounded at 24 × 0.5 s. Open-Meteo and
QWeather use `--connect-timeout 2 --max-time 5`, and the AppleScript is skipped
unless `pgrep -x BetterTouchTool` succeeds — `osascript` would otherwise
auto-launch a BTT the user had just quit.

### Shortcut Contract

The named Shortcut must end with a JSON dictionary in this shape:

```json
{"temperature":19.9,"humidity":54,"icon":"clear-day"}
```

`temperature` is Celsius, `humidity` is relative humidity as a percentage, and
`icon` is a canonical icon name or an Apple condition label such as `Mostly
Sunny`; the refresh normalises labels before caching. A missing, empty, or
malformed Shortcut result is a provider failure and never reaches the cache.

### Stored Shape

All providers are normalised into the shape BTT's own payload had, so the
render path never learns which one answered:

```json
{"currently":{"temperature":19.9,"humidity":0.54,"icon":"clear-night"}}
```

Humidity is a 0–1 fraction; temperature is Celsius.

### Rendering

| Mode | Stored value present | Stored value absent |
| --- | --- | --- |
| `--text` | `20°C` over `54%` (or `°F` after conversion) | `--` |
| `--icon` | the emoji below | nothing — BTT hides the widget |

WMO weather code to icon name, then icon name to emoji:

| WMO code | Icon name | Emoji |
| --- | --- | --- |
| 0 | `clear-day` / `clear-night` | ☀️ / 🌙 |
| 1, 2 | `partly-cloudy-day` / `partly-cloudy-night` | ⛅ / ☁️ |
| 3 | `cloudy` | ☁️ |
| 45, 48 | `fog` | 🌫️ |
| 51–55, 61–65, 80–82 | `rain` | 🌧️ |
| 56, 57, 66, 67 | `sleet` | 🌨️ |
| 71–77, 85, 86 | `snow` | ❄️ |
| 95 | `thunderstorm` | ⛈️ |
| 96, 99 | `hail` | 🌨️ |
| anything else | `cloudy` | ☁️ |

`is_day` picks the night variant of clear and partly-cloudy. The icon-name
layer exists because Apple's payload speaks those names, and an unmapped name
renders `🌡️`.

## Journey Contracts

### One Widget Tick

**Input:** the mode flag, the widget UUID, and `cache/weather.data.value`.

**Transformation:**

```text
read cached JSON (fresh or stale) -> consume the force flag
    -> start a detached refresh when stale or forced
    -> render text or icon from whatever was read
```

**Outputs:** widget JSON on stdout, plus one `widget` trace row
(`cached` / `stale` / `forced` / `empty`).

**Invariants:**

- The widget path never fetches. A stale value still prints, so the widgets
  always show the last readings while a detached refresh runs behind them.
- Both instances share the cache name `weather.data` and the refresh lock name
  `weather`, so one refresh serves both and the second instance never starts a
  duplicate.
- Terminal mode (no UUID) prints plain text.

**Example:**

```console
$ widgets/weather.sh --text
20°C
54%
```

## Implementation Map

| Contract | Stable owner |
| --- | --- |
| Both widgets, both modes, all providers | [`widgets/weather.sh`](../../widgets/weather.sh) |
| Cache, force flag, trace, publish | [`widgets/lib/btt-widget.sh`](../../widgets/lib/btt-widget.sh) |
| Intervals, shared tap action, the Now Playing tap script | [`bttpreset/Default.bttpreset`](../../bttpreset/Default.bttpreset) |

## Verification Map

No automated tests.

| Behaviour to verify | Focused evidence |
| --- | --- |
| Shared cache | Refresh from the text widget and confirm the icon widget redraws from the same `weather.data.value` without fetching. |
| Provider order | Run a valid `BTT Weather` Shortcut and confirm the trace records `source=shortcuts`; remove it and confirm QWeather answers. |
| Both down | Make the Shortcut invalid and block the network providers; confirm `refresh error bad-json`, exit 1, and that the previous value keeps printing. |
| Validation | Make the Shortcut return a null temperature and confirm QWeather answers rather than a `null` reaching the row. |
| Unit conversion | Set `BTT_WEATHER_UNIT=fahrenheit` and confirm the row switches to `°F` with the converted number. |
| Empty cache | Remove `cache/weather.data.value`; the text widget must print `--` and the icon widget nothing at all. |
| Icon mapping | Feed each WMO band and confirm the emoji, including the day/night split for codes 0–2. |

## Known Gaps

- No automated tests, and no recorded fixture of either provider's response.
- The coordinates default to a fixed point rather than the machine's location;
  a move needs `BTT_WEATHER_LAT`/`LON` set by hand.

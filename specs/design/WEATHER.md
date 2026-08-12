# Weather Widgets Specification

Indexed from [`specs/FEATURES.md`](../FEATURES.md). Caching, force flags,
dim-while-refreshing, and tracing belong to
[`specs/design/WIDGET-RUNTIME.md`](WIDGET-RUNTIME.md).

## Reconstruction Target

This document preserves what the two weather widgets show: one script in two
modes over one shared cached observation, which provider is asked first and
why, and how conditions become an emoji.

Open-Meteo's and Apple WeatherKit's response schemas beyond the fields read
below are external boundaries.

## Entry Points

| Entry point | Trigger | Contract |
| --- | --- | --- |
| `widgets/weather.sh <uuid> --text` | BTT widget "Weather", 600 s interval | Two rows: temperature over relative humidity. |
| `widgets/weather.sh <uuid> --icon` | BTT widget "Weath Icon", 600 s interval | One emoji for the current conditions. |
| `widgets/weather.sh <uuid> --refresh` | The detached refresh | Fetches conditions and stores one JSON observation. Exits 1 when both sources fail. |
| Tap on either → `actions/tap-refresh.sh <weather-uuid> <icon-uuid>` | User | Both refresh together; they share one cached observation. |

Flags may appear in any order, and the widget UUID is an ordinary positional
argument. An unrecognised `--flag` exits 2.

| Variable | Default | Meaning |
| --- | --- | --- |
| `BTT_WEATHER_UNIT` | `celsius` | `fahrenheit` converts on render; both sources report Celsius. |
| `BTT_WEATHER_TTL` | `300` | How old the stored observation may be before a refresh starts. |
| `BTT_WEATHER_LAT`, `BTT_WEATHER_LON` | `38.5`, `106.31` | Open-Meteo query point; taken from the coordinates BTT's own payload carried. |

## Feature Tree

```text
Weather widgets
├── One observation, two widgets
│   ├── Cache under weather.data, not under a per-widget name
│   ├── Render text or icon from the same JSON
│   └── Refresh both from either tap
├── Fetch, off the widget path
│   ├── Ask Open-Meteo first — no key, ~1 s, reachable here
│   ├── Fall back to BTT's get_weather (Apple WeatherKit)
│   ├── Normalise both into one shape
│   └── Never block the widget on the network or AppleScript
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
| Open-Meteo answers with a numeric temperature, humidity, and weather code | Use it | `refresh ok source=open-meteo t=… h=…` |
| Open-Meteo fails, times out, or returns a field that fails validation | BTT `get_weather` | `refresh ok source=btt t=… h=…` |
| Both fail, or the result is not valid JSON | Nothing stored | `refresh error bad-json`, exit 1 |

The BTT-native weather widget fetches from a provider this network cannot
reach — Clash drops it — which is why these widgets fetch for themselves.
`get_weather` remains as the fallback because it reaches Apple WeatherKit
through BTT itself.

Both fetches are bounded: Open-Meteo at `--connect-timeout 2 --max-time 5`,
`get_weather` at 24 × 0.5 s, and the AppleScript is skipped entirely unless
`pgrep -x BetterTouchTool` succeeds — `osascript` would otherwise auto-launch
a BTT the user had just quit.

### Stored Shape

Both providers are normalised into the shape BTT's own payload had, so the
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
| Both widgets, both modes, both providers | [`widgets/weather.sh`](../../widgets/weather.sh) |
| Cache, force flag, trace, publish | [`widgets/lib/btt-widget.sh`](../../widgets/lib/btt-widget.sh) |
| Intervals, shared tap action, the Now Playing tap script | [`bttpreset/Default.bttpreset`](../../bttpreset/Default.bttpreset) |

## Verification Map

No automated tests.

| Behaviour to verify | Focused evidence |
| --- | --- |
| Shared cache | Refresh from the text widget and confirm the icon widget redraws from the same `weather.data.value` without fetching. |
| Provider order | Block `api.open-meteo.com` and confirm the trace records `source=btt`. |
| Both down | Block both and confirm `refresh error bad-json`, exit 1, and that the previous value keeps printing. |
| Validation | Feed Open-Meteo JSON with a null temperature and confirm the fallback runs rather than a `null` reaching the row. |
| Unit conversion | Set `BTT_WEATHER_UNIT=fahrenheit` and confirm the row switches to `°F` with the converted number. |
| Empty cache | Remove `cache/weather.data.value`; the text widget must print `--` and the icon widget nothing at all. |
| Icon mapping | Feed each WMO band and confirm the emoji, including the day/night split for codes 0–2. |

## Known Gaps

- No automated tests, and no recorded fixture of either provider's response.
- The coordinates default to a fixed point rather than the machine's location;
  a move needs `BTT_WEATHER_LAT`/`LON` set by hand.

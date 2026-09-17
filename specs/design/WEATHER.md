# Weather Widgets Specification

Indexed from [`specs/FEATURES.md`](../FEATURES.md). Caching, force flags,
dim-while-refreshing, and tracing belong to
[`specs/design/WIDGET-RUNTIME.md`](WIDGET-RUNTIME.md).

## Reconstruction Target

This document preserves what the two weather widgets show: one script in two
modes over one shared cached observation, where the query point comes from,
which provider is asked first and why, and how conditions become an emoji.

The QWeather, Open-Meteo, and Apple WeatherKit response schemas beyond the
fields read below are external boundaries.

## Entry Points

| Entry point | Trigger | Contract |
| --- | --- | --- |
| `widgets/weather.sh <uuid> --text` | BTT widget "Weather", 600 s interval | Two rows: temperature over relative humidity. |
| `widgets/weather.sh <uuid> --icon` | BTT widget "Weather Icon", 600 s interval | One emoji for the current conditions. |
| `widgets/weather.sh <uuid> --refresh` | The detached refresh | Fetches conditions and stores one JSON observation. Stores nothing when every source fails. |
| `widgets/weather.sh --location` | Human, or `actions/doctor.sh` | Prints the query point (`lat,lon`) the point-based sources would use, or fails with `no query point: BTT reports no location`. |
| Tap on either → `actions/tap-refresh.sh <weather-uuid> <icon-uuid>` | User | Both refresh together; they share one cached observation. |

Flags may appear in any order, and the widget UUID is an ordinary positional
argument. An unrecognised `--flag` exits 2.

| Variable | Default | Meaning |
| --- | --- | --- |
| `BTT_WEATHER_UNIT` | `celsius` | `fahrenheit` converts on render; all sources report Celsius. |
| `BTT_WEATHER_TTL` | `300` | How old the stored observation may be before a refresh starts. |

The query point is **not** a variable: it is the Mac's own location (see
[Query Point](#query-point)).

An empty refresh writes a short-lived failure marker to stop detached redraws
from launching the same refresh every second while the cache remains stale.
The marker gates the refresh launch, not the source ladder: once it expires,
the next scheduled refresh walks the pipeline again, and a refresh that
succeeds writes a fresh value.

## Feature Tree

```text
Weather widgets
├── One observation, two widgets
│   ├── Cache under weather.data, not under a per-widget name
│   ├── Render text or icon from the same JSON
│   └── Refresh both from either tap
├── Fetch, off the widget path
│   ├── Query point from the system — BTT's get_location, never a variable
│   ├── Fall back to QWeather — domestic/direct route
│   ├── Fall back to Open-Meteo, then BTT's get_weather — both at that point
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
| QWeather answers at the resolved point | Use it | `refresh ok source=qweather t=… h=…` |
| QWeather fails or is not configured | Open-Meteo at the same point | `refresh ok source=open-meteo t=… h=…` |
| Open-Meteo fails or returns invalid fields | BTT `get_weather` at the same point | `refresh ok source=btt t=… h=…` |
| BTT reports no location | No point-based source is asked | `refresh empty` — the last observation keeps printing |
| All providers fail, or the result is not valid JSON | Nothing stored | `refresh empty`, and the last observation keeps printing |

An Apple Weather Shortcut bridge led this ladder until 2026-09-18, when it
was parked: it is a supported bridge to Apple Weather rather than a read of
the Weather widget's private storage, but a run that fails — for example on a
Mac with no location fix — raises an alert on every detached refresh and
floods the screen, which is too high a price for one of four sources. The
implementation and its JSON contract are in git history.

`get_location` and `get_weather` calls are bounded at 24 × 0.5 s, and every
one of them is skipped unless `pgrep -x BetterTouchTool` succeeds —
`osascript` would otherwise auto-launch a BTT the user had just quit.
Open-Meteo and QWeather use `--connect-timeout 2 --max-time 5`. One refresh
can therefore spend at most 34 s in provider calls, which is what the shared
refresh lock is sized against.

### Query Point

All three sources are asked about one point, and that point is the Mac the
widgets run on. BTT's `get_location` reports it — BTT is the app that hosts
these widgets and the only process here that can hold a location permission.
No environment variable or public-IP lookup can set it, so the row follows the
machine rather than a place chosen at setup time.

A location BTT cannot place comes back as prose rather than coordinates
(`no location available`), so the first two numbers in its answer are used only
when they form a valid pair: latitude within ±90 and longitude within ±180. If
that answer is not usable, the sources are skipped rather than queried about a
city nobody chose, so a machine without a fix shows no weather at all until it
has one.

`widgets/weather.sh --location` prints the resolved pair; `actions/doctor.sh`
shows it beside the other first-run findings, and names the setting to grant
when there is none.

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
- The refresh asks the system where the Mac is before it asks any provider,
  and no configuration can override that point. With no fix those providers
  are skipped, so a stale observation is never refreshed from a place the
  machine is not.
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
| Provider order | Unset `BTT_WEATHER_QW_KEY` and confirm the trace falls to `source=open-meteo`; then block the network and confirm it falls to `source=btt`. No run may raise a Shortcuts alert. |
| Query point | Compare `widgets/weather.sh --location` with the Mac's actual position; move the machine (or re-grant Location Services) and confirm the next refresh follows it rather than keeping the old city. |
| No location fix | With Location Services off for BTT, confirm the refresh records `refresh empty`, the row keeps its previous reading, and `widgets/weather.sh --location` reports the no-fix message. |
| Both down | Block every provider; confirm `refresh empty` and that the previous value keeps printing. |
| Validation | Make QWeather return a payload with a missing field; confirm Open-Meteo answers rather than a `null` reaching the row. |
| Unit conversion | Set `BTT_WEATHER_UNIT=fahrenheit` and confirm the row switches to `°F` with the converted number. |
| Empty cache | Remove `cache/weather.data.value`; the text widget must print `--` and the icon widget nothing at all. |
| Icon mapping | Feed each WMO band and confirm the emoji, including the day/night split for codes 0–2. |

## Known Gaps

- No automated tests, and no recorded fixture of either provider's response.
- The sources depend on BTT holding a location permission. Without
  it they never run, and nothing on the bar shows that: the row keeps its last
  reading, which looks healthy. `widgets/weather.sh --location` and
  `actions/doctor.sh` are the two places it is visible.
- The exact text a successful `get_location` returns is not recorded here. The
  parse accepts the first two in-range numbers in BTT's answer, which covers
  the `{LAT},{LON}` its own `get_weather` takes.

# Providers

External data providers the widgets depend on, with the facts that drove
each choice. Checked 2026-09-08; quota and pricing facts drift — re-verify
at the cited sources before acting on them.

## Weather

Constraint: this machine routes all traffic through Clash (rule mode, TUN
on, macOS system proxy set). Foreign domains go through the proxy node and
die when it times out; domestic domains ride the DIRECT rule and survive.
Verified: legacy CMA endpoints are gone (`www.weather.com.cn/data/sk/…` and
`/data/cityinfo/…` 301 to an HTML page; `d1/d4.weather.com.cn` JSONP dead).

| Provider | Domestic | Auth | Free tier | Status |
| --- | --- | --- | --- | --- |
| Apple Weather via Shortcuts | No | macOS Shortcut permissions | — | **Primary** |
| QWeather (和风天气) | Yes | API key (`?key=`) or JWT (Ed25519, ≤ 24 h `exp`) | 50k req/month (¥0 tier, Weather group) | Fallback |
| Open-Meteo | No | none | no key | Fallback |
| Apple WeatherKit (BTT `get_weather`) | No | none | — | Last resort |
| CMA `weather.com.cn` | Yes | none | — | Rejected: endpoints dead |
| Seniverse | Yes | key | unverified | Rejected for now |
| OpenWeatherMap, wttr.in | No | key / none | — | Rejected: same VPN dependency as Open-Meteo |

- QWeather API host is per-project (`*.qweatherapi.com`, shown in
  console.qweather.com); `devapi.qweather.com` serves legacy hosts only.
  Widget reads `BTT_WEATHER_QW_HOST` / `BTT_WEATHER_QW_KEY`.
- The default `BTT Weather` Shortcut is the Apple Weather bridge. It must end
  with a JSON object containing numeric Celsius `temperature`, numeric relative
  humidity percentage `humidity`, and a non-empty canonical weather `icon` name
  or Apple condition label such as `Mostly Sunny`; the widget normalises labels.
- Widget usage: 600 s refresh ≈ 4.4k req/month — ~11× inside the free tier.
- v7 `GET /v7/weather/now` returns `now.temp` / `now.humidity` as strings
  (°C, 0–100 %) and `now.icon` as a 100–515 code with **no day/night
  variants** (codes: [weather-conditions.csv](https://raw.githubusercontent.com/qwd/dev-site/master/assets/table/weather-conditions.csv)).
  Live responses still carry the legacy 15x night codes (150 clear,
  151–153 partly cloudy, 154 overcast) that the CSV lacks. Responses are
  gzip-compressed — curl needs `--compressed`.
  The docs mark v7 deprecated in favour of `GET /weather/v1/current/{lat}/{lon}`
  (JWT-only, different response shape) — the future migration target.
- API-key auth gets a daily request cap from 2027-01-01 (size unspecified);
  JWT is the escape hatch (official shell signing snippet in the auth docs).
- Attribution is required by QWeather terms; the widget records
  `source=qweather` in the refresh trace.

Sources: [Apple WeatherKit](https://developer.apple.com/weatherkit/),
[WeatherKit REST authentication](https://developer.apple.com/documentation/weatherkitrestapi/request-authentication-for-weatherkit-rest-api),
[Shortcuts command line](https://support.apple.com/guide/shortcuts-mac/use-shortcuts-from-the-command-line-apd455c82f02/mac),
[QWeather pricing](https://dev.qweather.com/en/docs/finance/pricing/),
[authentication](https://dev.qweather.com/en/docs/configuration/authentication/),
[weather-now API](https://dev.qweather.com/en/docs/api/weather/weather-now-webapi-v7/),
[current weather API](https://dev.qweather.com/en/docs/api/weather/weather-current/),
[conditions](https://dev.qweather.com/en/docs/api/weather/weather-conditions/).

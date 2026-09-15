# BTT — Touch Bar widgets for BetterTouchTool

Personal BetterTouchTool (BTT) configuration: Touch Bar script widgets
(`widgets/`), the tap and track-change actions they wire to (`actions/`),
and the exported preset (`bttpreset/`). Everything is plain shell and
Python — no plugin, no daemon, no background service.

Released under the [PolyForm Noncommercial 1.0.0 license](LICENSE) — free for
personal and non-commercial use. Not affiliated with BetterTouchTool or
Folivora — see [THIRD-PARTY.md](THIRD-PARTY.md).

<!-- TODO: hero screenshot of the Touch Bar row. Save it under docs/ and
     replace this comment with: ![Touch Bar](docs/touchbar.png) -->

## Layout

| Path | Contents |
| --- | --- |
| `widgets/` | One script per widget, plus `lib/` (shared widget runtime, quota widgets, Clash and MediaRemote helpers) and `lyrics/` (the Lyrics feature package) |
| `actions/` | Helpers invoked by widget taps, media keys, and setup, plus `lib/` (shared BTT-control support) |
| `bttpreset/` | Exported BTT preset (`Default.bttpreset`) — this is what you import |
| `specs/` | [What every widget must do](specs/FEATURES.md), [design documents](specs/design/), and measured [constraints](specs/CONSTRAINTS.md) |
| `tests/` | The Lyrics test suite — stdlib `unittest`, no dependencies |
| `cache/`, `logs/` | Runtime state and diagnostics, created automatically |

## Prerequisites

- BetterTouchTool with a Touch Bar (or Control Strip). BTT needs **Automation
  permission to drive itself** — widgets stay blank until it is granted, and
  macOS only prompts once, so see Troubleshooting if you missed the prompt.
- `zsh`, `curl`, Python 3.9+ (all ship with macOS, nothing to install).
- `jq` and `nowplaying-cli`:

  ```sh
  brew install jq nowplaying-cli
  ```

- `codexbar` for the Codex, Claude and OpenCode quota widgets — the rest work
  without it:

  ```sh
  brew install codexbar
  ```

- A running Clash/Mihomo controller for the Clash widgets — defaults target
  Clash Verge's Unix socket `/tmp/verge/verge-mihomo.sock` with the API at
  `http://127.0.0.1:9097` (all tunable, see Configuration).
- Apple Music for the Lyrics and Star widgets.

## Quick start

Clone to `~/Documents/BTT` — worth doing literally, because the exported
preset invokes every script through `$HOME/Documents/BTT/...`:

```sh
git clone https://github.com/zhenyulin/BTT.git ~/Documents/BTT
cd ~/Documents/BTT
```

Then, in order:

1. **Install the prerequisites** above.

2. **Build the MediaRemote helper.** This is the only compiled piece, and the
   repo deliberately ships source rather than the binary. It supplies the
   `isPlaying` flag, which nothing else publishes reliably — skip it and the
   Now Playing widget keeps the album cover where the play icon belongs while
   paused:

   ```sh
   clang -O2 actions/nowplaying-state.m -framework Foundation \
       -F/System/Library/PrivateFrameworks -framework MediaRemote \
       -o ~/Library/Application\ Support/BTT/nowplaying-state
   ```

3. **Configure weather (optional).** The weather widgets work with no key at
   all. To add QWeather as a fallback that still answers when the VPN node has
   timed out, copy the template and fill in a free key from
   [console.qweather.com](https://console.qweather.com):

   ```sh
   cp .env-template .env
   ```

4. **Run the preflight** — it checks the toolchain, the helper, BTT
   permissions and the repo layout, and prints the fix beside anything
   missing:

   ```sh
   actions/doctor.sh
   ```

5. **Back up your current BTT setup**, then import `bttpreset/Default.bttpreset`
   in BTT. The preset is named `Default` and also carries general BTT settings,
   so export your existing preset first if you have one worth keeping. (To
   configure widgets by hand instead, the table below lists every script and
   its wiring.)

6. **Point the widgets at themselves** — this sets the BTT persistent
   variables that taps and track changes use to find each widget:

   ```sh
   actions/set-widget-variables.sh
   ```

7. **Verify:** tap each widget — it greys out while its refresh runs, then
   redraws. `actions/doctor.sh` should now be all-green, and
   `widgets/now-playing-lyrics.sh --report` shows the shared trace.

The Star widget appears in the preset but has no script in this repo — it is
configured natively in BTT.

### Cloning somewhere else

Every script honours `BTT_REPO_DIR`, so the scripts themselves work from any
path. The preset is the exception: it hard-codes `$HOME/Documents/BTT` in its
script paths. Either clone to that path, or rewrite the preset before
importing:

```sh
sed -i '' "s|\$HOME/Documents/BTT|$PWD|g" bttpreset/Default.bttpreset
```

`actions/doctor.sh` reports a warning when your checkout is somewhere the
preset will not find.

## Widgets

| Widget | Shows | Script | Refresh | Tap action |
| --- | --- | --- | --- | --- |
| Lyrics | Synchronised lyrics + now playing | `widgets/now-playing-lyrics.sh` | 1 s | Repaint after a short delay |
| Now Playing | Title/album/artist + album-cover icon (the player's app icon when the track carries no cover; the play icon returns while paused, with slightly dimmed text) — only while an allowed player holds Now Playing (browsers ignored) | `widgets/now-playing.sh` | 1 s | `actions/now-playing-toggle.sh` + lyrics refresh |
| Codex | Codex quota | `widgets/codex-quota.sh` | 300 s | `actions/tap-refresh.sh` |
| Claude | Claude 5 h / 7 d quota | `widgets/claude-quota.sh` | 600 s | `actions/tap-refresh.sh` |
| OpenCode | OpenCode Go weekly quota | `widgets/opencode-quota.sh` | 300 s | `actions/tap-refresh.sh` |
| 🌐 | Selected Clash node's region flag | `widgets/clash-region.sh` | 300 s | `actions/tap-refresh.sh` (region + latency) |
| Latency | Selected Clash node's latency | `widgets/clash-latency.sh` | 300 s | `actions/tap-refresh.sh` (region + latency) |
| Star | Favourite (★/☆) — only while Apple Music holds Now Playing; hidden with the row when the track ends | AppleScript in the preset + `actions/now-playing-app.sh` | 10 s | Toggle favourite |
| Weather | Temperature/humidity | `widgets/weather.sh --text` | 600 s | `actions/tap-refresh.sh` (text + icon) |
| Weather Icon | Conditions icon | `widgets/weather.sh --icon` | 600 s | `actions/tap-refresh.sh` (text + icon) |

Their refresh asks Apple Weather through the no-prompt `BTT Weather` Shortcut
first, then QWeather (和风天气), a domestic API that Clash routes DIRECT, so it
keeps answering when the VPN node has timed out. Open-Meteo (no key) and BTT's
`get_weather` (Apple WeatherKit) remain later fallbacks. Those three later
sources all take a point, and the point is the Mac's own location as BTT
reports it, never a configured coordinate — see
[Where the weather is](#where-the-weather-is). The Shortcut must end
with `{"temperature":19.9,"humidity":54,"icon":"clear-day"}` or an Apple
condition label such as `Mostly Sunny`; the widget normalises the icon. QWeather
needs a free API key and API host from console.qweather.com (50k requests/month
free; see Configuration; provider facts in
[specs/reference/PROVIDERS.md](specs/reference/PROVIDERS.md)). Tapping either
one forces a shared detached refresh (they cache one `weather.data` value):
the tapped widget greys out while the refresh runs and the redraw that ends
it restores the normal color with the new value.

The native BTT Now Playing widget cannot be told to ignore specific apps —
it follows whichever app owns the system Now Playing session, browsers
playing YouTube included. The `now-playing.sh` script widget replaces it:
it prints the track only when the holder's bundle id is in
`BTT_NOW_PLAYING_ALLOWED` (an allowlist, not a denylist, because browsers
are many and players are few) and prints nothing otherwise, which makes
BTT hide it. It takes play/pause from the `nowplaying-state` helper's
`isPlaying`, because the published playback rate lies: QQ Music keeps
reporting rate 1 while paused, which left the album cover on screen where the
play icon belongs — the same finding
[`specs/design/LYRICS.md`](specs/design/LYRICS.md) records for the lyrics sampler. The
preset ships it in place of the native widget (same UUID,
so `BTT_WIDGET_NOW_PLAYING_UUID` keeps working): 1 s refresh, tap toggles
play/pause and refreshes the Lyrics widget, long-press toggles the `Music`
Touch Bar group.

The tap runs `actions/now-playing-toggle.sh` rather than BTT's own `Play or
Pause` action, for the reason the widget itself exists: that action sends the
media key, and macOS routes the media key to whichever app holds the Now
Playing session — so a tap paused the browser video that had taken the session
over and left the row's own track playing. The script resolves the player the
way the row does (`now-playing-app.sh`) and addresses it directly: Apple Music
over AppleScript, any other allowed player — which can only be the session
holder — with the media key through BTT.

The group toggle is a named trigger (`Toggle Music Group`)
running AppleScript, because BTT has no toggle action: it asks
`get_active_touch_bar_group` and then triggers either `Open Touch Bar Group
With Name` (205) or `Close currently open Touch Bar group` (191). Now
Playing is one of the widgets merged into groups, so the same long-press
closes the group from inside it. Long-pressing the Lyrics widget opens the
player (named trigger → `now-playing-app.sh`), which is where that action
used to live on Now Playing.

The track itself comes from whichever MediaRemote source can answer in full.
The `nowplaying-state` helper is asked first because it is the cheaper of the
two (~0.05 s against `nowplaying-cli`'s ~0.22 s, on a widget that runs every
second); when the session's holder does not publish the bundle id and the
artwork the helper needs to answer alone, `nowplaying-cli get-raw` supplies
them. Either way the dictionary is written to `cache/now-playing.raw.json`,
where the Lyrics sampler and the Star widget's gate read it instead of
querying MediaRemote again — see
[`widgets/lib/media-remote.sh`](widgets/lib/media-remote.sh).

Each widget script takes its BTT widget UUID as an optional first argument,
which taps and detached refreshes use to address the widget. Refresh
intervals above are as exported in the preset.

Media-key next/previous and the two-finger swipe triggers run
`actions/track-changed.sh`, which hands the expensive lyric work to a
detached process and then refreshes the Lyrics (and Star) widgets.

When the player closes, the three music widgets leave together: whichever side
notices first — the Now Playing widget's own tick or the Lyrics sampler —
empties the Lyrics and Star widgets in the same `osascript` that settles the
row. The Star widget would otherwise sit there alone until its 10 s
AppleScript tick came round.

## Actions

| Script | Purpose |
| --- | --- |
| `actions/doctor.sh` | First-run preflight: dependencies, the compiled helper, BTT permissions, repo layout |
| `actions/tap-refresh.sh` | Force one or more widgets to refresh now, even with a fresh cache |
| `actions/track-changed.sh` | Track-change hook: detached lyrics pre-warm + widget refresh |
| `actions/now-playing-app.sh` | Prints the current Now Playing holder's bundle id (MediaRemote) — gates the Star widget |
| `actions/now-playing-toggle.sh` | Now Playing tap: play/pause the player the row is showing, not whoever holds the session |
| `actions/set-widget-variables.sh` | Sets the BTT persistent variables mapping widget names to UUIDs |
| `actions/tap-restart.sh` | Date/Time widget tap: marks the traces before BTT's own restart action |
| `actions/btt-quit.sh` | Quit BTT for real — survives a wedged BTT and BTTRelaunch (below) |

## Configuration

Scripts read environment variables with built-in defaults; set them where
BTT's shell actions can see them (BTT environment variables, `~/.zshenv`, or
— weather widgets only — `$BTT_REPO_DIR/.env`).

| Variable | Default | Used by |
| --- | --- | --- |
| `BTT_REPO_DIR` | `~/Documents/BTT` | Every script (repo, cache, and log paths) |
| `BTT_LOG_DIR` | `$BTT_REPO_DIR/logs` | Logging |
| `CLASH_API`, `CLASH_SECRET`, `CLASH_SOCKET`, `CLASH_GROUP` | `http://127.0.0.1:9097`, `""`, `/tmp/verge/verge-mihomo.sock`, `PROXY` | Clash widgets (`widgets/lib/clash.sh`) |
| `CLASH_ICON`, `CLASH_FONT_COLOR` | — | Icon and colour for the Clash widgets |
| `CLASH_LATENCY_MIN_MS`, `CLASH_LATENCY_MAX_MS` | `150`, `500` | Latency colour bands |
| `CLAUDE_QUOTA_MAX_AGE`, `CODEX_QUOTA_MAX_AGE`, `OPENCODE_QUOTA_MAX_AGE` | `300` | Quota cache freshness (seconds) |
| `BTT_LYRICS_*` | — | Lyrics tunables — see [`specs/design/LYRICS.md`](specs/design/LYRICS.md) |
| `BTT_NOW_PLAYING_ALLOWED` | `com.apple.Music com.tencent.QQMusicMac` | Space-separated bundle ids allowed to hold the Now Playing row and to drive the Lyrics sampler's MediaRemote fallback (matched case-insensitively; Lyrics drops `com.apple.Music`, which it reads directly) |
| `BTT_NOW_PLAYING_STATE_BIN`, `BTT_NOW_PLAYING_CLI` | the compiled helper in BTT's support directory, `nowplaying-cli` on `PATH` | The two MediaRemote sources (`widgets/lib/media-remote.sh`); overriding either is how a fixture drives these scripts, since they set their own `PATH` |
| `BTT_NOW_PLAYING_RAW_PATH`, `BTT_NOW_PLAYING_RAW_MAX_AGE` | `$BTT_REPO_DIR/cache/now-playing.raw.json`, `5` | The raw Now Playing dictionary the Now Playing widget writes each tick, and how old the Lyrics sampler and the Star widget's gate may find it before asking for their own |
| `BTT_WEATHER_QW_HOST`, `BTT_WEATHER_QW_KEY` | — | Weather widgets: QWeather API host and key (console.qweather.com); unset either to skip QWeather and use the foreign sources only |
| `BTT_WEATHER_SHORTCUT` | `BTT Weather` | Weather widgets: no-prompt Apple Weather Shortcut name; its final output must be the documented JSON object |
| `BTT_WEATHER_UNIT`, `BTT_WEATHER_TTL` | `celsius`, `300` | Weather widgets: unit (celsius/fahrenheit) and weather cache TTL (seconds) |

### Where the weather is

The weather widgets ask BTT for the Mac's location instead of reading a
coordinate pair from the environment, so there is nothing to configure and
nothing that goes stale when the machine moves: QWeather, Open-Meteo and
BTT's own WeatherKit lookup all take that point, and `widgets/weather.sh
--location` prints the one in use. Without Location Services for
BetterTouchTool those three sources stay off and only the Apple Weather
Shortcut answers.

## The Lyrics feature

The Lyrics widget is the deepest feature: track sampling from Apple Music /
MediaRemote, lyric lookup across local LRC files, Apple Music's cached TTML,
LrcAPI and LRCLIB, and width-constrained rendering. Its behaviour contract
and configuration live in [`specs/design/LYRICS.md`](specs/design/LYRICS.md). Diagnostics:

```sh
widgets/now-playing-lyrics.sh --report      # render + trace health
widgets/now-playing-lyrics.sh --watch       # live tick stream
widgets/now-playing-lyrics.sh --diagnose    # full diagnosis
```

## Quitting BTT

Quitting BTT from its own menu does not stick when BTT is wedged: the quit
Apple Event never gets processed, and BTTRelaunch (BTT's own relauncher)
brings the process back if it dies without a graceful quit. Use
`actions/btt-quit.sh` to quit for real — it kills BTTRelaunch, then asks BTT
to quit and escalates to TERM/KILL if it hangs. The next launch of BTT
re-creates BTTRelaunch. The preset wires the Date Time widget to it: tap
restarts BTT (BTT's own restart action), long-press quits for real (named
action → `btt-quit.sh`).

## Docs

- [`specs/FEATURES.md`](specs/FEATURES.md) — every widget's observable
  behaviour, the rules all of them share, and the index of design documents.
- [`specs/design/`](specs/design/) — one document per feature: Now Playing,
  Lyrics, Star, the shared widget runtime, quotas, Clash, weather, and BTT
  control.
- [`specs/CONSTRAINTS.md`](specs/CONSTRAINTS.md) — the layout and stacking
  constraints every widget must respect.
- [`specs/NOTE.md`](specs/NOTE.md) — promoted repo learnings: empirical
  layout keys, preset rules, and correction patterns.

## Troubleshooting

Start with `actions/doctor.sh` — it covers most of the list below and prints
the fix beside each failure.

| Symptom | Cause and fix |
| --- | --- |
| Every widget is blank or never appears | BTT lacks Automation permission to script itself. Re-enable it under System Settings → Privacy & Security → **Automation** → BetterTouchTool; macOS only prompts once, so the checkbox may need ticking by hand. |
| A widget shows a short label instead of a value | That label *is* the failure: `NO CODEXBAR`, `No curl`, `Controller`, `--`, `🌐`. Install what it names, or leave it — a missing dependency never breaks the other widgets. |
| Now Playing shows the album cover while paused | The `nowplaying-state` helper is not built. Run the `clang` line in Quick start step 2 — without it nothing publishes the `isPlaying` flag, because the playback rate QQ Music publishes lies. |
| Weather shows the wrong city | The row follows the Mac, so this means BTT reported a location from the wrong place (or, after moving, one that is still cached). Check with `widgets/weather.sh --location`, then BTT's own location under System Settings > Privacy & Security > Location Services. |
| Tapping a widget does nothing | The BTT persistent variables are unset, so the tap cannot address the widget. Re-run `actions/set-widget-variables.sh` — and note this is required after importing a preset you have edited by hand. |
| The Lyrics widget is empty but music is playing | The player may not be allowed to hold the row; add its bundle id to `BTT_NOW_PLAYING_ALLOWED`. Browsers are excluded on purpose. Then check `widgets/now-playing-lyrics.sh --report`. |
| A widget stays grey | Grey means a refresh is in flight; it clears when the refresh ends. If it never clears, the refresh process died holding the lock — delete `cache/<widget-name>.refreshing` and `cache/<widget-uuid>.force`. |
| Quitting BTT does not stick | Known BTT behaviour when it is wedged. Use `actions/btt-quit.sh`, which kills BTTRelaunch first. |
| Nothing works after moving the checkout | The preset hard-codes `$HOME/Documents/BTT`. See *Cloning somewhere else*. |

Deeper diagnostics for the Lyrics feature:

```sh
widgets/now-playing-lyrics.sh --report      # render + trace health
widgets/now-playing-lyrics.sh --watch       # live tick stream
widgets/now-playing-lyrics.sh --diagnose    # full diagnosis
```

## Testing

The Lyrics package carries a test suite that needs nothing installed — plain
stdlib `unittest`, because the widgets themselves run under whatever `python3`
BTT's `PATH` finds:

```sh
tests/run.sh                    # everything
tests/run.sh lyrics.test_lrc    # one module
```

Every path the package reads is redirected into a temporary directory before
Python starts, so a run never touches your `cache/` or `logs/`.

## Uninstall

1. In BTT, delete the imported preset (or restore the preset you exported
   before importing it).
2. `rm -rf ~/Documents/BTT` — everything else lives inside the checkout. Only
   `cache/` and `logs/` are written elsewhere, and they are inside it.
3. Optionally delete `~/Library/Application Support/BTT/nowplaying-state`.
4. Remove the Homebrew packages you installed for it —
   `brew uninstall nowplaying-cli jq codexbar` — if nothing else uses them.

## Contributing

Issues and pull requests are welcome. Before opening a PR:

- Run `tests/run.sh` — it must stay green.
- Run `actions/doctor.sh` if you touched anything BTT-facing.
- Keep widget behaviour consistent with [`specs/FEATURES.md`](specs/FEATURES.md);
  if you change intended behaviour, change the spec in the same PR. The spec is
  the contract, and the widgets are expected to be rebuilt from it.
- Match the surrounding style: shell in `zsh` with `set -u`, comments that
  explain *why* rather than *what*, and no new dependencies where a shell
  builtin or the standard library will do.

## License

[PolyForm Noncommercial 1.0.0](LICENSE). Free for personal, hobby, study, and
other non-commercial use, including use by non-commercial organizations.
Commercial use — including resale or paid redistribution of the widgets or the
preset — is not permitted, and copyright remains with the author.

Third-party trademarks, services and dependencies are covered separately in
[THIRD-PARTY.md](THIRD-PARTY.md).

## Acknowledgements

The Lyrics widget would be much poorer without the community APIs it falls
back to — [LRCLIB](https://lrclib.net) and [LrcAPI](https://github.com/AprilForApril/LrcApi) —
and without [`nowplaying-cli`](https://github.com/kirtan-shah/nowplaying-cli),
which is the only thing that exposes the Now Playing session holder's bundle id
and album artwork.

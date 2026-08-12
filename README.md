# BTT — Touch Bar widgets for BetterTouchTool

Personal BetterTouchTool (BTT) configuration: Touch Bar script widgets
(`widgets/`), the tap and track-change actions they wire to (`actions/`),
and the exported preset (`bttpreset/`). Everything is plain shell and
Python — no plugin, no daemon, no background service.

## Layout

| Path | Contents |
| --- | --- |
| `widgets/` | One script per widget, plus `lib/` (shared widget runtime, Clash helpers) and `lyrics/` (the Lyrics feature package) |
| `actions/` | Helpers invoked by widget taps, media keys, and setup |
| `bttpreset/` | Exported BTT preset (`Default.bttpreset`) and `backup.bttpreset` |
| `cache/`, `logs/` | Runtime state and diagnostics, created automatically |

## Prerequisites

- BetterTouchTool with a Touch Bar (or Control Strip), with Automation
  permission for BTT to drive the widgets.
- `zsh`, `curl`, Python 3 (all ship with macOS); `jq` and `nowplaying-cli`
  (`brew install jq nowplaying-cli`).
- `codexbar` (`brew install codexbar`) for the Codex, Claude and OpenCode
  quota widgets.
- A running Clash/Mihomo controller for the Clash widgets — defaults target
  Clash Verge's Unix socket `/tmp/verge/verge-mihomo.sock` with the API at
  `http://127.0.0.1:9097` (all tunable, see Configuration).
- Apple Music for the Lyrics and Star widgets.

## Quick start

1. Clone or copy this repo to `~/Documents/BTT` — every script defaults to
   that path (`BTT_REPO_DIR` overrides).
2. Install the prerequisites above.
3. In BTT, import `bttpreset/Default.bttpreset`. (To configure widgets by
   hand instead, the table below lists every script and its wiring.)
4. Set the BTT widget UUID variables so taps and track changes can find the
   widgets: `./actions/set-widget-variables.sh`.
5. Verify: tap each widget — it greys out while its refresh runs, then
   redraws; `widgets/now-playing-lyrics.sh --report` shows the shared trace.

The Star widget appears in the preset but has no script in this repo — it is
configured natively in BTT.

## Widgets

| Widget | Shows | Script | Refresh | Tap action |
| --- | --- | --- | --- | --- |
| Lyrics | Synchronised lyrics + now playing | `widgets/now-playing-lyrics.sh` | 1 s | Repaint after a short delay |
| Now Playing | Title/album/artist + album-cover icon (the player's app icon when the track carries no cover; small play icon while paused) — only while an allowed player holds Now Playing (browsers ignored) | `widgets/now-playing.sh` | 1 s | Play or Pause + weather/lyrics refresh |
| Codex | Codex quota | `widgets/codex-quota.sh` | 300 s | `actions/tap-refresh.sh` |
| Claude | Claude 5 h / 7 d quota | `widgets/claude-quota.sh` | 600 s | `actions/tap-refresh.sh` |
| OpenCode | OpenCode Go weekly quota | `widgets/opencode-quota.sh` | 300 s | `actions/tap-refresh.sh` |
| 🌐 | Selected Clash node's region flag | `widgets/clash-region.sh` | 300 s | `actions/tap-refresh.sh` (region + latency) |
| Latency | Selected Clash node's latency | `widgets/clash-latency.sh` | 300 s | `actions/tap-refresh.sh` (region + latency) |
| Star | Favourite (★/☆) — only while Apple Music holds Now Playing | AppleScript in the preset + `actions/now-playing-app.sh` | 10 s | Toggle favourite |
| Weather | Temperature/humidity | `widgets/weather.sh --text` | 600 s | `actions/tap-refresh.sh` (text + icon) |
| Weath Icon | Conditions icon | `widgets/weather.sh --icon` | 600 s | `actions/tap-refresh.sh` (text + icon) |

Their refresh asks Open-Meteo first (no key, ~1 s, since the BTT-native
weather provider is unreachable on this network) and falls back to BTT's
`get_weather` (Apple WeatherKit). Tapping either one forces a shared
detached refresh (they cache one `weather.data` value): the tapped widget
greys out while the refresh runs and the redraw that ends it restores the
normal color with the new value.

The native BTT Now Playing widget cannot be told to ignore specific apps —
it follows whichever app owns the system Now Playing session, browsers
playing YouTube included. The `now-playing.sh` script widget replaces it:
it prints the track only when the holder's bundle id is in
`BTT_NOW_PLAYING_ALLOWED` (an allowlist, not a denylist, because browsers
are many and players are few) and prints nothing otherwise, which makes
BTT hide it. It reads the track from `nowplaying-cli get-raw` (the only
source carrying the holder's bundle id) but takes play/pause from the
`nowplaying-state` helper's `isPlaying`, because the published playback rate
lies: QQ Music keeps reporting rate 1 while paused, which left the album
cover on screen where the play icon belongs — the same finding
[`specs/design/LYRICS.md`](specs/design/LYRICS.md) records for the lyrics sampler. The
preset ships it in place of the native widget (same UUID,
so `BTT_WIDGET_NOW_PLAYING_UUID` keeps working): 1 s refresh, tap toggles
play/pause and refreshes the Lyrics widget, long-press toggles the `Music`
Touch Bar group. The toggle is a named trigger (`Toggle Music Group`)
running AppleScript, because BTT has no toggle action: it asks
`get_active_touch_bar_group` and then triggers either `Open Touch Bar Group
With Name` (205) or `Close currently open Touch Bar group` (191). Now
Playing is one of the widgets merged into groups, so the same long-press
closes the group from inside it. Long-pressing the Lyrics widget opens the
player (named trigger → `now-playing-app.sh`), which is where that action
used to live on Now Playing.

Each widget script takes its BTT widget UUID as an optional first argument,
which taps and detached refreshes use to address the widget. Refresh
intervals above are as exported in the preset.

Media-key next/previous and the two-finger swipe triggers run
`actions/track-changed.sh`, which hands the expensive lyric work to a
detached process and then refreshes the Lyrics (and Star) widgets.

## Actions

| Script | Purpose |
| --- | --- |
| `actions/tap-refresh.sh` | Force one or more widgets to refresh now, even with a fresh cache |
| `actions/track-changed.sh` | Track-change hook: detached lyrics pre-warm + widget refresh |
| `actions/now-playing-app.sh` | Prints the current Now Playing holder's bundle id (MediaRemote) — gates the Star widget |
| `actions/set-widget-variables.sh` | Sets the BTT persistent variables mapping widget names to UUIDs |
| `actions/tap-restart.sh` | Date/Time widget tap: marks the traces before BTT's own restart action |
| `actions/btt-quit.sh` | Quit BTT for real — survives a wedged BTT and BTTRelaunch (below) |

## Configuration

Scripts read environment variables with built-in defaults; set them where
BTT's shell actions can see them (BTT environment variables or `~/.zshenv`).

| Variable | Default | Used by |
| --- | --- | --- |
| `BTT_REPO_DIR` | `~/Documents/BTT` | Every script (repo, cache, and log paths) |
| `BTT_LOG_DIR` | `$BTT_REPO_DIR/logs` | Logging |
| `CLASH_API`, `CLASH_SECRET`, `CLASH_SOCKET`, `CLASH_GROUP` | `http://127.0.0.1:9097`, `""`, `/tmp/verge/verge-mihomo.sock`, `PROXY` | Clash widgets (`widgets/lib/clash.sh`) |
| `CLASH_ICON`, `CLASH_FONT_COLOR` | — | Icon and colour for the Clash widgets |
| `CLASH_LATENCY_MIN_MS`, `CLASH_LATENCY_MAX_MS` | `150`, `500` | Latency colour bands |
| `CLAUDE_QUOTA_MAX_AGE`, `CODEX_QUOTA_MAX_AGE`, `OPENCODE_QUOTA_MAX_AGE` | `300` | Quota cache freshness (seconds) |
| `BTT_LYRICS_*` | — | Lyrics tunables — see [`specs/design/LYRICS.md`](specs/design/LYRICS.md) |
| `BTT_NOW_PLAYING_ALLOWED` | `com.apple.Music com.tencent.QQMusicMac` | Now Playing widget: space-separated bundle ids allowed to hold the row (matched case-insensitively) |
| `BTT_WEATHER_UNIT`, `BTT_WEATHER_TTL` | `celsius`, `300` | Weather widgets: unit (celsius/fahrenheit) and get_weather cache TTL (seconds) |

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
- [`specs/NOTE.md`](specs/NOTE.md) — empirical notes on BTT's own layout keys
  and the preset files.

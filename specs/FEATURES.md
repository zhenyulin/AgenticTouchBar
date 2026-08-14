# Features

The behaviour-level reconstruction contract for this repository: every Touch
Bar widget, the actions they wire to, and the rules all of them obey. Each
substantial feature links to its own design document under
[`specs/design/`](design/).

Setup and configuration live in the root [`README.md`](../README.md); the
measured failure modes and layout limits live in
[`specs/CONSTRAINTS.md`](CONSTRAINTS.md).

## Reconstruction Target

What must remain observable: which widget draws what, when it draws nothing,
what a tap or a gesture does, which files carry state between processes, and
what each widget shows when a dependency is missing.

Deliberately excluded: BetterTouchTool's private implementation, preset
styling (fonts, paddings, colours of the bar itself), and provider protocols
beyond the fields the widgets read.

The boundary is one machine:

```text
BTT triggers + gestures
        -> shell and Python widget scripts
        -> cache/ and logs/
        -> BTT Touch Bar
```

Nothing here runs as a service. Every process is started by BTT, by a tap, or
by another script, and exits.

## Entry Points

| Widget | Shows | Script | Interval | Tap | Long press |
| --- | --- | --- | --- | --- | --- |
| Now Playing | Title over artist - album, album side by minimax width, with cover/app/play icon (slight dim while paused) | `widgets/now-playing.sh` | 1 s | Play or Pause, then refresh Lyrics | Toggle the `Music` group |
| Lyrics | The synchronised lyric line | `widgets/now-playing-lyrics.sh` | 1 s | Repaint itself after 1.8 s | Open Player |
| Star | `★` / `☆` for Apple Music | preset AppleScript + `actions/now-playing-app.sh` | 10 s | Toggle favourite | — |
| Claude | 5 h and 7 d quota | `widgets/claude-quota.sh` | 600 s | Refresh itself | — |
| Codex | Quota and time to reset | `widgets/codex-quota.sh` | 300 s | Refresh itself | — |
| OpenCode | Weekly quota | `widgets/opencode-quota.sh` | 300 s | Refresh itself | — |
| 🌐 | Selected Clash node's flag | `widgets/clash-region.sh` | 300 s | Refresh region and latency | — |
| VPN | Selected Clash node's latency and label | `widgets/clash-latency.sh` | 300 s | Refresh region and latency | — |
| Weather | Temperature over humidity | `widgets/weather.sh --text` | 600 s | Refresh both weather widgets | — |
| Weath Icon | Conditions emoji | `widgets/weather.sh --icon` | 600 s | Refresh both weather widgets | — |
| Date / Time | The clock (BTT-native) | — | — | `actions/tap-restart.sh` + BTT restart | Quit BTT for real |

Intervals are as exported in
[`bttpreset/Default.bttpreset`](../bttpreset/Default.bttpreset). Every widget
script takes its BTT widget UUID as an optional first argument and behaves as
an ordinary command-line program without it.

### Actions And Bindings

| Trigger | Runs | Purpose |
| --- | --- | --- |
| Any widget tap | `actions/tap-refresh.sh [--delay-ms N] <uuid>...` | Force one or more widgets to refresh now. |
| Two-finger swipe left / right | `Previous` / `Next`, then `actions/track-changed.sh` | Media control plus the lyric pre-warm and repaint. |
| Single-finger swipe left / right | Volume down / up | — |
| Middle mouse button | Mission Control | — |
| After the Mac wakes | AppleScript `refresh_widget` ×4 | Refreshes VPN, Codex, Claude, and OpenCode at once. |
| Install, once | `actions/set-widget-variables.sh` | Maps widget names to UUIDs as BTT persistent variables. |

## Feature Tree

```text
BTT Touch Bar
├── The music row — specs/design/NOW-PLAYING.md, LYRICS.md, STAR.md
│   ├── Show the track only while an allowed player holds Now Playing
│   ├── Show the synchronised lyric beside it, within a fixed width
│   ├── Show and toggle the Apple Music favourite
│   ├── Hand the row over cleanly on a track change
│   └── Play, pause, skip, and open the player from the bar
├── The status row — specs/design/QUOTA.md, CLASH.md, WEATHER.md
│   ├── Coding-assistant quotas, coloured by what is left
│   ├── The selected proxy node's region and latency
│   └── Local conditions, fetched without a key
├── The shared widget runtime — specs/design/WIDGET-RUNTIME.md
│   ├── Read a cached value on the tick; compute it detached
│   ├── Dim a widget for exactly as long as its refresh runs
│   ├── Turn a tap into one forced refresh
│   └── Record every run in one shared trace
└── Managing BTT itself — specs/design/BTT-CONTROL.md
    ├── Name every widget's UUID
    ├── Restart BTT, marked in the traces
    └── Quit BTT so that it stays quit
```

## Cross-Cutting Contracts

These hold for every widget and are not repeated in the designs.

| Contract | Consequence |
| --- | --- |
| **One script runner.** BTT runs every shell widget through a single `BetterTouchToolShellScriptRunner` XPC service. | A widget that blocks for *n* seconds stops every other widget for *n* seconds. Nothing slow may run on a widget's own path. |
| **Empty text hides.** BTT removes a script widget whose output is empty. | Hiding is not a mode; it is what printing nothing means. Now Playing, Lyrics, Star, Weath Icon, and OpenCode all use it. |
| **Detach into a session.** Background work uses `btt_spawn_detached`, never a bare `nohup … &`. | A stuck child cannot wedge future runs of its own widget. |
| **Atomic writes.** Every file another process reads is written aside and renamed. | No reader ever sees a half-written value, marker, or state file. |
| **Failures are values.** A missing dependency prints a short label (`NO CODEXBAR`, `No curl`, `Controller`, `--`, `🌐`). | The bar always shows something, and the trace records the outcome. |
| **UUIDs come from variables.** The preset interpolates `{BTT_WIDGET_*_UUID}`. | `actions/set-widget-variables.sh` must run before any tap can address a widget. |
| **Terminal mode is read-only.** Without a UUID a widget prints plain text. | Any widget can be run and diffed from a shell without touching BTT state. |
| **The scrollable zone paints under the pinned zone.** | A widget whose frame reaches the right-pinned zone is drawn under it — a BTT layout rule, not a bug. |

## Design Index

| Design | Owns |
| --- | --- |
| [`design/NOW-PLAYING.md`](design/NOW-PLAYING.md) | The allowlist gate, playing/paused detection, icon choice, row layout, and the track handover that clears Lyrics. |
| [`design/LYRICS.md`](design/LYRICS.md) | Track sampling, lyric lookup across six sources, matching, and the one-second render state machine. |
| [`design/STAR.md`](design/STAR.md) | The favourite toggle and the holder lookup shared with Open Player. |
| [`design/WIDGET-RUNTIME.md`](design/WIDGET-RUNTIME.md) | Cache, refresh lock, force flag, colour, publish, and the shared trace. |
| [`design/QUOTA.md`](design/QUOTA.md) | The three `codexbar` widgets and the reset-progress colouring. |
| [`design/CLASH.md`](design/CLASH.md) | Node resolution, region glyphs, latency measurement and history. |
| [`design/WEATHER.md`](design/WEATHER.md) | Two widgets over one observation, Open-Meteo first, and the disabled hide-while-playing rule. |
| [`design/BTT-CONTROL.md`](design/BTT-CONTROL.md) | Widget variables, the recorded restart, and the quit that sticks. |

## State Index

Everything under `cache/` and `logs/` is created on demand and is safe to
delete; a rebuild costs one refresh cycle.

| Path | Written by | Owning design |
| --- | --- | --- |
| `cache/<name>.value`, `.refreshing`, `<uuid>.force`, `<name>.quota-reset` | The shared runtime | [`WIDGET-RUNTIME.md`](design/WIDGET-RUNTIME.md) |
| `cache/now-playing-artwork-*`, `now-playing-player-*`, `now-playing.identity` | `now-playing.sh` | [`NOW-PLAYING.md`](design/NOW-PLAYING.md) |
| `cache/lyrics-cleared` | `now-playing.sh`, read by the Lyrics tick | [`NOW-PLAYING.md`](design/NOW-PLAYING.md) |
| `cache/lyrics/state.json`, per-track records, locks | The Lyrics package | [`LYRICS.md`](design/LYRICS.md) |
| `cache/weather.data.value` | `weather.sh` | [`WEATHER.md`](design/WEATHER.md) |
| `logs/trace.tsv` | Every shell widget | [`WIDGET-RUNTIME.md`](design/WIDGET-RUNTIME.md) |
| `logs/lyrics/trace.tsv`, `logs/lyrics/render.json` | The Lyrics package | [`LYRICS.md`](design/LYRICS.md) |
| `logs/latency-history.tsv` | `clash-latency.sh` | [`CLASH.md`](design/CLASH.md) |
| `logs/btt-codexbar.log` | The quota widgets | [`QUOTA.md`](design/QUOTA.md) |
| `logs/btt-control.log` | `tap-restart.sh`, `btt-quit.sh` | [`BTT-CONTROL.md`](design/BTT-CONTROL.md) |

`cache/lyrics/state.json` is read by three different consumers — the Lyrics
widget, `now-playing.sh`, and `now-playing-app.sh` — which makes its shape the
most load-bearing informal contract in the repository.

## Verification

```sh
tests/run.sh                            # the lyrics test suite (stdlib unittest)
widgets/now-playing-lyrics.sh --report  # render + trace health, every widget
widgets/now-playing-lyrics.sh --watch   # live tick stream
widgets/now-playing-lyrics.sh --diagnose
```

Automated coverage exists only for the Lyrics package's pure decisions
([`design/LYRICS.md`](design/LYRICS.md#verification-map)). Every other widget
is verified by running it in terminal mode and by the checks each design's
verification map names.

## Known Gaps

- Only the Lyrics package has tests. The shell widgets, the shared runtime,
  and every preset-embedded script are unverified except by inspection.
- The Star widget's logic and three tap scripts live in the exported preset,
  where nothing lints or diffs them; a re-export can change behaviour silently.
- `cache/lyrics/state.json` is consumed by two scripts outside the Lyrics
  package with the age and source rules copied into each, rather than shared.
- Widget UUIDs are duplicated between `actions/set-widget-variables.sh`, the
  preset, and one hard-coded pair in the Now Playing tap's AppleScript.
- No `VISION.md` exists; the root `README.md` currently carries that role.

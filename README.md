# BTT

Personal BetterTouchTool configuration: Touch Bar widgets (`widgets/`), the
tap/track-change actions they wire up to (`actions/`), and the exported
preset (`bttpreset/`).

## Known issue: tap-refresh freezes

**Symptom:** widgets stop updating and tap-refresh does nothing until BTT
restarts. Two distinct causes produce this, with different scopes: one only
ever silences the Lyrics widget while everything else keeps ticking (fixed,
see below); the other silences *every* widget at once, including ones with
no network or Apple Music dependency at all (not fixable from this repo,
mitigated by restarting BTT).

### Root cause

`actions/track-changed.sh` backgrounds its Python helper
(`widgets/now-playing-lyrics.sh --track-changed`) to keep BTT's shell-script
runner from blocking on it.
Both of those helpers call Apple Music over AppleScript
(`read_apple_music()` in `widgets/lyrics/apple_music.py`), and until this was
fixed, both backgrounded with a plain `nohup … &`.

That's not actually detached. In a non-interactive shell — which every BTT
script action is — `cmd & disown` still leaves the child in the exact
process group BTT launched for the script; `setopt monitor` needed for real
job control fails without a controlling terminal. If BTT's single
`BetterTouchToolShellScriptRunner` XPC service ever waits on or signals by
process group rather than by the one pid it started, a child stuck in that
group can block every future invocation of that same script behind it.

When Music.app stops answering Apple Events — which it does periodically in
this environment, per `~/Library/Caches/BTTNowPlayingLyrics/error.log`
(`Apple Music query timed out`, and Music.app itself crashed at least once) —
killing the client-side `osascript` process on its own timeout doesn't
guarantee it actually exits; it can be stuck in an uninterruptible wait on
Music's reply. Left in BTT's shared process group, that's enough to wedge
the lyrics widget's own ordinary 1-second tick behind it. Measured in the
logs: a single incident silenced the lyrics widget's trace for **26
minutes**, while every other widget kept ticking on schedule the entire
time — proof this specific, 26-minute-long pattern was these two
un-detached call sites, not the separate freeze below.

### A second, unrelated freeze: BetterTouchTool's own main thread

The fix above does not explain every freeze. `widgets/timer-widget.sh` timer
widget has no network or Apple Music dependency: with every other widget
disabled, it still froze
— twice, for 312s and 174s — proving a second failure mode exists that has
nothing to do with any script in this repo.

`actions/freeze-catch.sh` watches the shared trace and runs `sample` against
both BetterTouchTool and its `BetterTouchToolShellScriptRunner` XPC helper
the moment it goes silent, catching one of these live
(`~/Library/Caches/btt-widgets/freeze-samples/20260808-180758/`). The
runner's only thread was idle in `mach_msg2_trap`, waiting for a request that
never arrived — it wasn't stuck on anything. BTT's own main thread was:
100% of the sample's 2332 samples sat inside AppKit, recursing through
`-[NSWindow recalculateKeyViewLoop]` → `NSPerformVisuallyAtomicChange` →
`-[NSView _layoutSubtreeWithOldSize:]` dozens of frames deep while decoding
a NIB. That blocks BTT's run loop — the same run loop that dispatches widget
ticks to the shell-script runner — so nothing runs anywhere, for however
long that layout pass takes, until it finishes or `BTTRelaunch` (BTT's own
bundled watchdog) gives up and restarts it.

There is nothing to patch here: it's a performance bug in BetterTouchTool's
own AppKit code, not in any script this repo controls. See "Stopgap" below
for the mitigation, and "Diagnostics" for the two tools that isolated and
captured it.

### The real fix

`widgets/lib/btt-widget.sh` now exposes `btt_spawn_detached`, a small
Python double-fork (`os.fork()` + `os.setsid()` + `os.execvp()`) that moves
the child into its own session, out of BTT's process group, while staying in
the same `BTT -> zsh -> python -> target` lineage BTT already has file
access for (a `launchd` job would dodge the process-group issue too, but
macOS's TCC blocks a `launchd`-spawned process from this repo's files under
`~/Documents` — see the freeze-guard section below for where that bit us
again). `btt_refresh_detached` (used by every other widget) already worked
this way; `track-changed.sh` uses the same primitive instead of a bare
`nohup … &`.

### Stopgap: `actions/btt-freeze-guard.sh`

BTT already restarts itself on a freeze via `BTTRelaunch`, its own bundled
watchdog — but on the AppKit freeze above, that took 4-5 minutes each time,
which is where this guard earns its keep: a faster, logged restart. It runs
every 5 seconds, actively refreshes the timer widget, and waits up to five
seconds for a new timer trace row. If that probe fails, the guard treats BTT
as unresponsive and restarts immediately. Responsive probes retain the
separate three-minute preventive-restart cadence and may defer that restart
once while macOS has seen keyboard or pointer activity in the prior 60
seconds.

The shared trace remains useful for diagnostics and supplies the timer probe's
evidence: a newer `timer-widget` row means BTT dispatched the refresh.

- **Source of truth:** `actions/btt-freeze-guard.sh` in this repo.
- **Deployed copy:** `~/Library/Application Support/BTT/btt-freeze-guard.sh`.
  `launchd` runs the LaunchAgent below as its own TCC-authorized process,
  which macOS blocks from reading `~/Documents` — the same restriction
  `btt_spawn_detached`'s comment describes, hit again one level up. **After
  editing the script in this repo, re-copy it to the deployed path:**

  ```sh
  cp actions/btt-freeze-guard.sh ~/Library/Application\ Support/BTT/btt-freeze-guard.sh
  ```

- **Scheduler:** `~/Library/LaunchAgents/com.zhenyulin.btt-freeze-guard.plist`,
  `StartInterval` 5s, loaded via `launchctl bootstrap gui/$(id -u) …`.
- **Logic:** refresh timer-widget every five seconds and wait up to
  `BTT_TIMER_REFRESH_TIMEOUT` seconds for its trace row. A timeout restarts
  BTT even during active input; a responsive BTT may still defer the separate
  three-minute preventive restart for one interval (`MAX_CONSECUTIVE_DEFERS`).
  After a restart, the guard reopens BTT without taking foreground focus and
  retries `refresh_widget` for each script widget at 5, 10, and 15 seconds,
  then allows a 30-second startup grace period before resuming probes.

  The defer used to be uncapped: `freeze-guard.log` showed 43 consecutive
  "deferred -- active user" checks in a row (2026-08-08 23:53 to 2026-08-09
  01:06), and `trace.tsv` had a 603s freeze gap inside that exact window --
  ordinary activity in some other app was being treated as evidence BTT
  itself was fine, which it isn't. This is almost certainly why restarts
  weren't happening reliably. The cap bounds the worst case to two 3-minute
  intervals instead of running until an idle gap happens to appear.
- **Logs:**
  - `logs/freeze-guard.log` — records every five-second probe with its latest
    complete `timer-widget` trace row, plus the final row observed before a
    restart and every deferred preventive restart.
  - `logs/freeze-catch.log` — records each manual probe with its latest timer
    row; `logs/freeze-samples/<timestamp>/context.txt` preserves that final
    row and the last 20 shared-trace records alongside live process samples.
  - `~/Library/Caches/btt-widgets/freeze-guard.std{out,err}.log` — general
    run output, for debugging the guard itself.

Manage the LaunchAgent with:

```sh
# reload after editing the plist or redeploying the script
launchctl bootout gui/$(id -u)/com.zhenyulin.btt-freeze-guard 2>/dev/null
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.zhenyulin.btt-freeze-guard.plist

# check status
launchctl print gui/$(id -u)/com.zhenyulin.btt-freeze-guard

# remove entirely
launchctl bootout gui/$(id -u)/com.zhenyulin.btt-freeze-guard
rm ~/Library/LaunchAgents/com.zhenyulin.btt-freeze-guard.plist
```

The lyrics-specific fix above means this guard should no longer see freezes
caused by that call site. The AppKit freeze is a separate, open issue in
BTT itself, so expect this to keep firing occasionally until upstream fixes
it — its log is how you'd notice if it starts firing more often than that,
which would mean a new cause.

### Diagnostics

Two tools built to isolate and capture the AppKit freeze, kept for any
future recurrence or regression:

- **`widgets/timer-widget.sh`** — a Touch Bar timer widget with no network or
  Apple Music dependency. It displays the elapsed time since the current BTT
  process started, resetting to zero after a BTT restart. A tap still greys it
  out for a few seconds (via the same refresh-lock coloring every widget uses,
  see `widgets/lib/btt-widget.sh`) without resetting the timer. If the timer
  stops while a next-song action still repaints the now-playing widget, the
  action's detached path is alive but ordinary BTT widget dispatch or input
  handling is not. Currently disabled in BTT (`BTTEnabled2: 0` in the preset)
  — re-enable it to test again after a BTT update, or if tap-refresh misbehaves
  in a new way.
- **`actions/freeze-catch.sh`** — run manually in a terminal
  (`./actions/freeze-catch.sh`) and left open. Actively probes timer-widget
  every five seconds; the moment it fails to produce a trace row within its
  timeout, it runs `sample` against both BetterTouchTool and
  `BetterTouchToolShellScriptRunner` in parallel and saves the output to
  `logs/freeze-samples/<timestamp>/` — a real stack trace of the freeze while
  it's still happening. This is what caught the sample referenced above.

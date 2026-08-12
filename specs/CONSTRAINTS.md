# Constraints

## Scope

Behavioural limits and operational constraints of the BTT widget setup: the
two freeze failure modes that have been measured in this environment, the
rules they impose on any script change, the Touch Bar stacking constraint
that bounds widget widths, and the diagnostics that catch them.
Setup and usage live in the root [`README.md`](../README.md).

## Freeze failure modes

Widgets stop updating and tap-refresh does nothing until BTT restarts. Two
distinct causes produce this, with different scopes:

| | Cause A: undetached background children | Cause B: BTT's own main thread |
| --- | --- | --- |
| Scope | Lyrics widget only; everything else keeps ticking | Every widget at once, including ones with no network or Apple Music dependency |
| Fixable in this repo | Yes — fixed via `btt_spawn_detached` | No — BTT's AppKit code; worked around by the freeze guard until the 2026-08-12 BTT upgrade fixed it upstream |

### Cause A: undetached background children

`actions/track-changed.sh` backgrounds its Python helper
(`widgets/now-playing-lyrics.sh --track-changed`) to keep BTT's shell-script
runner from blocking on it. Both helpers call Apple Music over AppleScript
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

**Constraint:** background work spawned from a BTT script action must
detach into its own session via `btt_spawn_detached` — a Python double-fork
(`os.fork()` + `os.setsid()` + `os.execvp()`) that moves the child into its
own session, out of BTT's process group, while staying in the same
`BTT -> zsh -> python -> target` lineage BTT already has file access for.
Never use a bare `nohup … &`. A `launchd` job would dodge the process-group
issue too, but macOS's TCC blocks a `launchd`-spawned process from this
repo's files under `~/Documents` — see the retired freeze guard below for
where that bit us again. `btt_refresh_detached` (used by every other widget)
already worked this way; `track-changed.sh` uses the same primitive.

### Cause B: BetterTouchTool's own main thread

The fix above does not explain every freeze. `widgets/timer-widget.sh` timer
widget has no network or Apple Music dependency: with every other widget
disabled, it still froze — twice, for 312s and 174s — proving a second
failure mode exists that has nothing to do with any script in this repo.

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

**Constraint:** nothing to patch in this repo — a performance bug in
BetterTouchTool's own AppKit code. Fixed upstream: the BTT version running
since 2026-08-12 no longer shows it, which is why the guard below is
retired. If widgets start freezing together again, that is the signal to
bring it back rather than to look for a new cause in this repo.

## Retired stopgap: the freeze guard

**Retired 2026-08-12**, when BTT was upgraded to a version that no longer
shows the AppKit freeze above. `actions/btt-freeze-guard.sh`, its
`actions/hid-state.c` helper, the deployed copies under
`~/Library/Application Support/BTT/`, and the
`com.zhenyulin.btt-freeze-guard` LaunchAgent were all removed; the script is
recoverable from git history if the freeze ever returns. What it did and
what it taught, kept because both bound any future attempt:

- **What it was.** A LaunchAgent watchdog, probing every 10s, that restarted
  BTT when the Touch Bar stopped moving. BTT already restarts itself on a
  freeze via `BTTRelaunch`, its own bundled watchdog — but that took 4-5
  minutes each time, which is where a faster, logged restart earned its keep.
- **Why it lived outside the repo.** `launchd` ran it as its own
  TCC-authorized process, which macOS blocks from reading `~/Documents` —
  the same restriction `btt_spawn_detached`'s comment describes, hit again
  one level up. So the source of truth lived here and the running copy at
  `~/Library/Application Support/BTT/btt-freeze-guard.sh`, re-copied by hand
  after every edit. Any future background service inherits that constraint,
  and with it the deploy step that is easy to forget.
- **Two independent signals, because one was not enough.** The widget
  heartbeat (newest `timer-widget`/`clash-latency` row in `logs/trace.tsv`)
  proved BTT was dispatching ticks at all; the lyrics render deadline
  (`logs/lyrics/render.json`) proved the display was still changing, since a
  widget that reprints its previous frame is indistinguishable from a
  healthy one in the heartbeat alone. An earlier probe that forced a Clash
  latency refresh and watched its history file restarted BTT roughly every
  two minutes while BTT was demonstrably fine — that file only gets a row
  after a network round trip, so every slow probe read as a freeze.
- **Thresholds have to be measured, not guessed.** The heartbeat timeout was
  raised from 45s to 90s on 2026-08-11: the restart ledger showed ~7
  restarts/hour over 12h with a median detected gap of 72s, while genuine
  wedges ran 15-30min (p90 930s) and some 60-90s pauses resolved on their
  own. Every restart costs a Touch Bar outage, a timer-widget reset, and
  mid-gesture input risk.
- **Never terminate BTT mid-gesture.** Its event tap sits between the
  hardware and every other app, so killing it while a modifier or button is
  held can leave the front app with a latched Shift or a phantom mouse-down.
  `HIDIdleTime` cannot answer "is something held right now" — modifiers do
  not auto-repeat and a paused drag posts nothing, so both read as idle
  within a second — which is why `hid-state.c` asked the window server
  directly.
- **A deferral is a delay, not a veto.** The input checks postponed a
  restart rather than cancelling it, capped at 60s. Uncapped, the log showed
  43 consecutive "deferred -- active user" checks (2026-08-08 23:53 to
  2026-08-09 01:06) around a 603s freeze gap in `trace.tsv`: activity in
  some *other* app was being read as evidence BTT was fine.
- **Not every stuck display was BTT's fault.** A `no_sample` or `locked`
  lyrics tick means the Apple Music sampler or the widget's own lock is
  stuck; restarting BTT fixes neither and costs an outage, so those were
  logged and left alone.

Its logs are left in place as the historical record: `logs/freeze-guard.log`
(per-probe verdicts), `logs/restart.tsv` (the structured restart ledger),
`logs/freeze-guard-state.json` (last per-probe assessment), and
`~/Library/Caches/btt-widgets/freeze-guard.std{out,err}.log`. `STATS.md`
analyses them. Nothing writes to any of these any more — the manual restart
and quit actions now log to `logs/btt-control.log`.

## Diagnostics

Two tools isolate and capture the AppKit freeze, kept for any future
recurrence or regression:

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

## Touch Bar stacking

BTT lays the Touch Bar out in three fixed zones: left-pinned
(`BTTTouchBarItemPlacement` 0), scrollable middle (1), and right-pinned (2).
The right-pinned zone always paints above the scrollable zone, and no
configuration changes that — BTT's preset format has no z-order key.
`BTTOrder` sorts items within a zone only; `BTTDisplayOrder` positions
right-pinned items relative to the Control Strip; neither affects paint order
across zones. Verified empirically (2026-08): reordering the Now Playing and
Lyrics widgets after every right-pinned widget, giving them the lowest display
order, and moving them into the right-pinned zone all left the stacking
unchanged — placement 2 additionally right-aligned them, which is why the
music widgets stay in the scrollable middle.

**Constraint:** a scrollable widget whose frame reaches into the right-pinned
zone is drawn *under* the latency and quota widgets — visible when the Now
Playing widget is wide. Now Playing and Lyrics therefore share the scrollable
width, and the row is budgeted to fit it: the lyrics viewport splits
`LYRIC_WIDTH_BUDGET_PX` (485) between the two, and the Now Playing widget's
own limits (`BTTTouchBarLine1MaxChars`, `BTTTouchBarLine2MaxChars`,
`BTTTBWidgetWidth`) must keep its frame inside the scrollable area (~600 px on
this bar). Extending Now Playing past that budget is what pushes content under
the pinned group; it is a BTT layout rule, not a bug to work around.

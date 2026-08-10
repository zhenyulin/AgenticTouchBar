# Lyrics Feature Specification

## Reconstruction Target

This document preserves the behavior needed to rebuild the Lyrics Touch Bar
feature: how BetterTouchTool invokes it, how playback state becomes a lyric
frame, how lyric sources are selected, and how slow or unavailable dependencies
are handled.

It is a behavior contract rather than a file inventory. It records stable
module and script owners where they help reconstruction, but leaves provider
protocol details, BetterTouchTool's private implementation, and presentation
styling choices to the implementation unless they affect an observable
contract.

The feature boundary is:

```text
BetterTouchTool / Music / MediaRemote
        -> track sample and widget trigger
Lyrics package
        -> cached, synchronized, width-constrained frame
BetterTouchTool Touch Bar
```

## Entry Points

| Entry point | Intended user or trigger | Contract |
| --- | --- | --- |
| `widgets/now-playing-lyrics.sh` | BetterTouchTool's periodic script widget | Sets the package import path and executes `python3 -m lyrics` in default widget mode. |
| `python3 -m lyrics` | The default widget invocation | Renders one frame and exits without querying Apple Music directly. |
| `python3 -m lyrics --sample` | Detached sampler | Queries the available player and atomically persists the newest track sample. |
| `python3 -m lyrics --track-changed <uuid>` | `actions/track-changed.sh` after next/previous media input | Follows the asynchronous track transition, pre-warms lyrics, and requests a final repaint. |
| `python3 -m lyrics --fetch <key>` | Detached cache worker | Fetches and persists one track's lyric record. |
| `python3 -m lyrics --measure` | Detached viewport worker | Measures the current Now Playing layout and persists viewport metrics. |
| `python3 -m lyrics --report`, `--watch`, `--diagnose` | Operator diagnostics | Reads render and trace state to inspect widget health and freeze shape. |
| `python3 -m lyrics --clear-current` | Operator or recovery action | Clears current lyric output/state according to the CLI implementation. |
| BetterTouchTool `refresh_widget` | Tap, play/pause, or track-change action | Causes BTT to invoke the widget again; the refresh request is asynchronous from shell actions. |

The production widget is configured in
[`bttpreset/Default.bttpreset`](../bttpreset/Default.bttpreset#L235). The shell
entry point is [`widgets/now-playing-lyrics.sh`](../widgets/now-playing-lyrics.sh#L1),
and the track-change bridge is
[`actions/track-changed.sh`](../actions/track-changed.sh#L1).

## Feature Tree

```text
Lyrics Touch Bar feature
├── Keep playback context current
│   ├── Read the newest persisted Apple Music sample
│   ├── Advance playback position by sample age
│   ├── Start a detached sampler when state is old
│   ├── Fall back to nowplaying-cli after Apple Music permission failure
│   └── Fall back to MediaRemote when Music is stopped or unavailable
├── Render the correct playback state
│   ├── Permission warning
│   ├── Paused or idle hidden widget
│   ├── Settling-track title placeholder
│   ├── Synchronized lyric line
│   ├── Instrumental, not-found, or cache-error status
│   └── Previous-frame preservation while work is pending
├── Find lyrics with bounded work
│   ├── Reuse a compatible per-track cache record
│   ├── Revive not-found records from Apple Music's local cache
│   ├── Prefer local LRC files
│   ├── Search Apple Music's cached TTML
│   ├── Apply the Mandopop catalog title when available
│   ├── Race LrcAPI and LRCLIB with bounded waiting
│   └── Accept only a sufficiently matched synchronized/instrumental result
├── Fit lyrics beside Now Playing
│   ├── Measure BTT-like text width in a detached JXA helper
│   ├── Persist track-specific viewport metrics
│   ├── Wrap at punctuation and spaces into at most two rows
│   ├── Preserve continuation indentation
│   └── Marquee rows that still overflow
├── Survive repeated one-second ticks
│   ├── Serialize widget ticks with an expiring lock
│   ├── Serialize one track fetch with a per-track lock
│   ├── Use atomic JSON/text replacement
│   ├── Retry not-found and cache-error records after bounded delays
│   └── Preserve the last visible frame on contention or pending work
├── Respond to user actions
│   ├── Repaint after play/pause state changes
│   ├── Follow next/previous until the title changes or the deadline expires
│   ├── Pre-warm the new track's lyrics cache
│   └── Repaint Lyrics and the Star widget after track change
└── Explain failures
    ├── Emit a user-visible permission or cache status
    ├── Record render receipts and trace outcomes
    ├── Keep provider and sampler errors out of the widget process
    └── Supply evidence for distinguishing widget failure from BTT freeze
```

## Decision Trees

### Main Render Flow

The normal widget tick is a synchronous, bounded decision over persisted
state. Samplers, viewport measurement, and lyric fetches are detached so the
tick does not wait for AppleScript, network, or text measurement work.

```mermaid
flowchart TD
    A[BTT invokes python3 -m lyrics] --> B[Acquire widget lock]
    B -->|lock unavailable| C[Emit last output; trace locked]
    B -->|lock acquired| D[Read persisted track sample]
    D -->|no usable sample| E[Start sampler if stale; emit last output; trace no_sample]
    D -->|sample available| F{Track state}
    F -->|denied| G[Emit "⚠ Allow BTT → Music"]
    F -->|paused| H[Emit empty text; trace paused]
    F -->|stopped, not_running, or no title| I[Emit empty text; trace idle]
    F -->|placeholder title| J[Emit "♪ <title>"; trace placeholder]
    F -->|playing real track| K[Build versioned track key]
    K --> L[Ensure viewport measurement in background]
    L --> M[Read compatible cache]
    M -->|not_found plus Apple cache match| N[Promote Apple cache record to ok]
    M -->|cache miss| O[Start background fetch; emit last output; trace pending]
    M -->|retry_after elapsed| P[Delete record; refetch; render waiting status]
    M -->|usable record| Q[Select active LRC line; wrap or marquee; emit frame]
    N --> Q
    P --> Q
    C --> R[Release lock; write receipt and trace]
    E --> R
    G --> R
    H --> R
    I --> R
    J --> R
    O --> R
    Q --> R
```

The lock is released and the render receipt is written in the `finally` path,
including a crash outcome. This is an operational invariant: a failed tick
must not leave the widget permanently locked.

### Playback Sampling

| Input or condition | Selected path | Observable result |
| --- | --- | --- |
| Persisted sample is younger than the refresh threshold | Project playback using sample age | The position advances without an AppleScript call in the widget tick. |
| Sample is old but still within the maximum usable age | Start a detached sampler and use the old sample | The current frame remains usable while fresh state is acquired. |
| Sample is older than the usable-age limit or absent | Start a sampler and return no usable track | The last rendered frame is reprinted; no blank intermediate frame is introduced. |
| Apple Music responds with permission denied | Try `nowplaying-cli` | A usable alternate sample may continue playback; otherwise the denied state is rendered. |
| Music is stopped or not running | Try MediaRemote | A non-Music player may supply the track; otherwise the widget becomes idle. |
| AppleScript fails for a reason other than permission denial | Keep the previous sample | The widget avoids replacing valid state with an unverified failure. |
| MediaRemote changes title or artist | Reset its locally tracked position | Subsequent playback position starts from the new track's sample. |
| MediaRemote cannot observe an in-player seek | Keep its projected position | Seeking is a known limitation of this fallback. |

Playback state is persisted atomically as a current snapshot. It is not a
history of tracks; lyric history belongs in per-track cache records.

### Track-Change Flow

`actions/track-changed.sh` must be detached from the BTT shell action because
waiting in the shared runner can block other widgets.

| Branch | Condition | Result |
| --- | --- | --- |
| Start | Next/previous action supplies the Lyrics UUID | Follow Apple Music samples at the configured interval. |
| Settling | Title is empty, unchanged, or a known placeholder | Persist the sample, continue until the deadline, and allow a repaint of position/state. |
| Settled | Title differs from the previous title and is not a placeholder | Pre-warm the track if its cache is absent, request a BTT repaint, and trace `settled`. |
| Timeout | Deadline expires without a settled title | Request a repaint anyway and trace `timeout`; do not invent a track identity. |
| Sampling error | One sample raises an exception | Log the failure, wait for the next interval, and keep following until settled or timeout. |

The small example command is the actual action shape:

```sh
actions/track-changed.sh <lyrics-widget-uuid> <star-widget-uuid>
```

The generic `tap-refresh.sh` call creates `.force` files for supplied UUIDs,
but the Python Lyrics path responds to the actual `refresh_widget` request and
does not call the shared shell `btt_force_pending()` consumer. Therefore
Lyrics and Star force markers are coordination residue rather than a required
part of their refresh contract.

### Lyric Source Dispatch

| Order | Decision | Next path |
| --- | --- | --- |
| 1 | A compatible cache record exists and its retry deadline has not arrived | Render the cached record. |
| 2 | A cached `not_found` record now has a matching Apple Music TTML record | Convert and atomically promote the local record to `ok`, then render it. |
| 3 | A local `.lrc` record matches | Use the local record without remote access. |
| 4 | Apple Music's `Cache.db` contains matching recent TTML | Convert TTML to LRC and use it. |
| 5 | A Chinese catalog title is available | Add it as the remote search title, without replacing the original track identity. |
| 6 | LrcAPI returns an acceptable result within its preference window | Prefer LrcAPI. |
| 7 | LrcAPI times out, fails, or returns no acceptable result | Await LRCLIB, then use a later acceptable LrcAPI result if available. |
| 8 | No provider returns an acceptable result | Persist `not_found` or `cache_error` according to the failure, then retry after its configured delay. |

Provider lookup is bounded and concurrent. LrcAPI and LRCLIB are started
together; provider-specific query retries must not turn the one-second widget
tick into a blocking network request.

### Candidate Acceptance

For every provider candidate:

1. Normalize title, artist, album, and relevant aliases.
2. Require synchronized lyrics or an explicit instrumental result.
3. Score title, artist, album, and duration similarity.
4. Accept only a score of at least `0.60`.
5. Continue to the next candidate/provider when the candidate is rejected.

An accepted result is a track association, not merely valid LRC syntax. A
provider response that parses but belongs to another recording must remain
unaccepted.

### LRC and Viewport Rendering

| Input | Decision | Result |
| --- | --- | --- |
| No active line at the current position | Select the preceding timed line when available | Keep the lyric frame synchronized to playback. |
| Line fits the measured width | Emit the line, and the next line when the pair fits | Use the compact two-line frame. |
| Line is too wide | Break at punctuation, then spaces | Preserve readable rows up to the two-row limit. |
| A row still exceeds width | Crop/marquee according to elapsed playback time | Scroll at the configured cell rate after the delay. |
| Viewport measurement is pending or fails | Use the carried/stored width or fallback width | Lose measurement accuracy for the track, not all layout state. |
| Track has no synchronized lyrics but is instrumental | Render the instrumental marker | Emit `♬`. |
| Track is not found | Render the not-found marker | Emit `♩`. |
| Cache/provider failure is persisted | Render the cache-error marker | Emit `⚠ Apple lyrics cache error`. |

LRC parsing must preserve timestamp semantics: offsets, enhanced timestamps,
multiple timestamps on one line, sorting, and same-timestamp deduplication.

## Journey Contracts

### One Widget Tick

**Input:** an invocation of `python3 -m lyrics` with a persisted track sample,
cache directory, and optional previous output.

**Transformation:**

```text
sample -> state gate -> track key -> viewport/cache lookup
       -> background work when needed -> LRC line selection
       -> width-constrained output
```

**Outputs:** one text frame to stdout, plus a render receipt and trace record.
Empty text is meaningful: BetterTouchTool hides the script widget when the
player is paused or idle.

**Invariants:**

- A widget tick does not directly call Apple Music or remote providers.
- At most one widget tick owns `widget.lock` at a time.
- A cache miss preserves the previous frame while fetch work runs.
- Lock release, receipt writing, and trace writing occur on both success and
  failure paths.
- Reads never observe partial JSON/text because state writes are atomic.

**Examples:**

```text
state=paused          -> stdout: ""; trace outcome: paused
state=denied          -> stdout: "⚠ Allow BTT → Music"
placeholder track    -> stdout: "♪ <title>"
missing lyric cache   -> previous stdout; trace outcome: pending
```

### Track Fetch

**Input:** a real track metadata record and its versioned cache key.

**Transformation:** local LRC -> Apple Music TTML cache -> catalog-assisted
remote lookup -> concurrent LrcAPI/LRCLIB candidate selection.

**Outputs:** an atomically written record with one of the supported outcomes:
usable lyrics, instrumental, `not_found`, or `cache_error`.

**State and timing:** positive and instrumental records are reusable;
`not_found` retries after six hours and `cache_error` retries after 90 seconds.
The fetch deadline and per-track lock bound concurrent work.

**Failure recovery:** malformed cache JSON is logged and removed; provider
exceptions become a persisted cache error or a later provider fallback; a
later Apple Music cache match can revive a previous `not_found` record.

### Track-Change Repaint

**Input:** a next/previous media action and the Lyrics widget UUID.

**Transformation:** detached polling samples -> title-settlement decision ->
optional cache pre-warm -> BTT `refresh_widget`.

**Outputs:** the next widget tick sees the new persisted track, and diagnostics
record either `settled` or `timeout`.

**Properties:** detached, bounded to the configured follow deadline, safe to
repeat, and non-blocking to the BTT shell runner.

### Durable and Ephemeral State

| State class | Examples | Semantics |
| --- | --- | --- |
| Reusable durable state | Per-track lyric JSON, `state.json`, `last.txt`, `lyrics.value`, `viewport.json`, `media_remote_position.json` | Survives a process and avoids repeating expensive work. |
| Diagnostic durable state | `error.log`, `logs/lyrics/trace.tsv`, `logs/lyrics/render.json` | Explains outcomes and separates widget failures from BTT scheduling failures. |
| Ephemeral coordination | `widget.lock`, `sampler.lock`, per-track locks, detached helpers, fetch deadlines | Prevents overlap and bounds work; stale locks are recoverable. |
| Integration residue | BTT UUID `.force` files | Intended for shared shell widgets; Lyrics and Star currently do not consume them. |

State writes must be atomic where another process can read the file during a
tick. Current-snapshot state may be overwritten by a newer sample; per-track
cache records are keyed so one track cannot replace another track's lyrics.

## Implementation Map

| Contract | Stable owner |
| --- | --- |
| Package entry and CLI dispatch | [`widgets/lyrics/__main__.py`](../widgets/lyrics/__main__.py#L1), [`widgets/lyrics/cli.py`](../widgets/lyrics/cli.py#L244) |
| BTT shell integration | [`widgets/now-playing-lyrics.sh`](../widgets/now-playing-lyrics.sh#L1), [`bttpreset/Default.bttpreset`](../bttpreset/Default.bttpreset#L235) |
| Track sampling and player fallback | [`widgets/lyrics/apple_music.py`](../widgets/lyrics/apple_music.py#L42), [`widgets/lyrics/media_remote.py`](../widgets/lyrics/media_remote.py#L1), [`widgets/lyrics/cli.py`](../widgets/lyrics/cli.py#L180) |
| Main state machine and output decisions | [`widgets/lyrics/render.py`](../widgets/lyrics/render.py#L146) |
| Widget lock, helper spawning, retry display timing | [`widgets/lyrics/locking.py`](../widgets/lyrics/locking.py#L1) |
| Cache identity and atomic persistence | [`widgets/lyrics/cache.py`](../widgets/lyrics/cache.py#L1), [`widgets/lyrics/metadata.py`](../widgets/lyrics/metadata.py#L183) |
| Provider order and bounded concurrency | [`widgets/lyrics/fetch.py`](../widgets/lyrics/fetch.py#L22), [`widgets/lyrics/concurrency.py`](../widgets/lyrics/concurrency.py#L1) |
| Local LRC and Apple Music cache adapters | [`widgets/lyrics/providers/local.py`](../widgets/lyrics/providers/local.py#L1), [`widgets/lyrics/providers/apple_cache.py`](../widgets/lyrics/providers/apple_cache.py#L1) |
| Remote provider adapters | [`widgets/lyrics/providers/lrcapi.py`](../widgets/lyrics/providers/lrcapi.py#L1), [`widgets/lyrics/providers/lrclib.py`](../widgets/lyrics/providers/lrclib.py#L1) |
| Candidate normalization and acceptance | [`widgets/lyrics/metadata.py`](../widgets/lyrics/metadata.py#L48), [`widgets/lyrics/matching.py`](../widgets/lyrics/matching.py#L1) |
| LRC parsing and active-line selection | [`widgets/lyrics/lrc.py`](../widgets/lyrics/lrc.py#L1), [`widgets/lyrics/layout.py`](../widgets/lyrics/layout.py#L1) |
| Viewport measurement and persistence | [`widgets/lyrics/viewport.py`](../widgets/lyrics/viewport.py#L1), [`widgets/lyrics/text_width.js`](../widgets/lyrics/text_width.js#L1) |
| Output shaping and last-frame preservation | [`widgets/lyrics/output.py`](../widgets/lyrics/output.py#L1) |
| Track-change workflow | [`actions/track-changed.sh`](../actions/track-changed.sh#L1), [`widgets/lyrics/cli.py`](../widgets/lyrics/cli.py#L100) |
| Runtime diagnostics | [`widgets/lyrics/diagnostics.py`](../widgets/lyrics/diagnostics.py#L1), [`widgets/lyrics/debug.py`](../widgets/lyrics/debug.py#L1) |

## Verification Map

No automated test suite or test configuration is currently present. A rebuild
should add focused tests around the pure decisions first, then integration
checks around detached processes and BTT.

| Behavior to verify | Focused evidence |
| --- | --- |
| State gate | Feed denied, paused, idle, placeholder, and playing snapshots to the render decision and compare exact output/status strings. |
| Last-frame preservation | Run a tick with no usable sample or a cache miss and verify stdout remains the prior output. |
| Cache safety | Write valid, malformed, `not_found`, and `cache_error` records; verify atomic replacement, cleanup, and retry deadlines. |
| Matching | Exercise aliases, CJK normalization, duration differences, instrumental candidates, and the `0.60` acceptance threshold. |
| Provider fallback | Make local, Apple cache, LrcAPI, and LRCLIB paths succeed/fail in order; verify the first acceptable result wins. |
| LRC timing | Parse offsets, enhanced timestamps, duplicate timestamps, and an empty/no-line interval. |
| Layout | Verify punctuation/space wrapping, two-row limits, continuation indentation, and marquee delay/rate at narrow and fallback widths. |
| Concurrency | Hold widget and per-track locks; verify the old frame is emitted and stale locks can be removed. |
| Track changes | Run `actions/track-changed.sh` with a synthetic UUID; verify detached execution, `settled`/`timeout`, pre-warm behavior, and repaint request. |
| BTT integration | Verify periodic invocation, play/pause clearing, track-change repaint, and widget hiding on empty output against the preset. |
| Diagnostics | Compare `logs/lyrics/render.json`, `logs/lyrics/trace.tsv`, and the shared widget trace during a single-widget failure and a BTT-wide freeze. |

Useful operator checks are `python3 -m lyrics --report`,
`python3 -m lyrics --diagnose`, and inspection of the render and trace files.
These are observational checks, not substitutes for deterministic unit tests.

## Known Gaps

- There are no automated tests, so the branch contracts above are reconstructed
  from implementation and operational evidence rather than regression-tested.
- The exact BetterTouchTool scheduling and `refresh_widget` guarantees are
  external behavior. The repository can request a repaint but cannot prove BTT
  will dispatch it during an AppKit or shell-runner freeze.
- Apple Music, `nowplaying-cli`, MediaRemote, LrcAPI, LRCLIB, OpenCC, and
  Apple Music's `Cache.db` are external dependency boundaries. Their response
  schemas, permissions, rate limits, and availability need integration checks.
- `BTT_LYRICS_LOCAL_DIR`, widget UUID variables, cache paths, and timing values
  are configuration inputs; the accepted deployment values are not captured
  in one versioned configuration contract.
- `actions/tap-refresh.sh` creates `.force` markers for Lyrics and Star, but
  those widgets do not call the shared shell marker consumer. The intended
  cleanup/ownership decision remains open: remove those marker writes for
  non-shell widgets or implement an explicit consumer.
- MediaRemote cannot observe in-player seeks, and Apple Music placeholder
  transitions are inherently asynchronous; callers must preserve `unknown`,
  `timeout`, and unavailable states rather than inventing track identity.
- The README documents a BetterTouchTool/AppKit freeze outside this repository.
  The diagnostic traces can identify it, but no Lyrics implementation change
  can repair that upstream scheduling failure.

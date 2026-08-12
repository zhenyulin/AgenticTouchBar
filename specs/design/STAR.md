# Star Widget Specification

Indexed from [`specs/FEATURES.md`](../FEATURES.md). The holder-resolution rule
it shares with the Now Playing widget is in
[`specs/design/NOW-PLAYING.md`](NOW-PLAYING.md).

## Reconstruction Target

This document preserves the favourite toggle beside the Now Playing row: when
the star appears at all, what a tap does to Apple Music and to the widget, and
how the same holder lookup also decides which app the Open Player gesture
opens.

The widget itself has no script in this repository — it is an AppleScript
widget configured in the preset. Both scripts are reproduced here as the
contract; the preset is the source of truth.

## Entry Points

| Entry point | Trigger | Contract |
| --- | --- | --- |
| Star widget script | BTT AppleScript widget, 10 s interval | Returns `★`, `☆`, or `""`. Empty text makes BTT hide the widget. |
| Star widget tap | User | Toggles `favorited` on Apple Music's current track and repaints the widget immediately. |
| `actions/now-playing-app.sh` | The two scripts above and the Open Player trigger | Prints one bundle id, or nothing. |
| `Open Player` named trigger | Long press on the Lyrics widget | Activates the app that bundle id names. |
| `actions/track-changed.sh` | Media next/previous, two-finger swipes | Refreshes the Star widget along with Lyrics. |

The widget is trigger `A05C5D37-7EAA-4F7B-AC00-23183CC8C6A1` in
[`bttpreset/Default.bttpreset`](../../bttpreset/Default.bttpreset), merged
into Touch Bar groups.

## Feature Tree

```text
Star widget
├── Appear only for Apple Music
│   ├── Resolve the Now Playing holder from MediaRemote
│   ├── Accept Apple Music playing behind another session holder
│   └── Return "" for anything else, so BTT hides the widget
├── Show the current track's favourite state
│   ├── ★ when favorited
│   ├── ☆ when not
│   └── "" while Music is stopped or not running
└── Toggle on tap
    ├── Paint the new symbol before Apple Music has persisted it
    ├── Write `favorited` on the current track
    └── Never return "missing value"
```

## Decision Trees

### Which App Holds The Row

`actions/now-playing-app.sh`, in order:

| Condition | Printed |
| --- | --- |
| MediaRemote's `kMRMediaRemoteNowPlayingInfoClientBundleIdentifier` is `com.apple.Music` | `com.apple.Music` |
| Otherwise, `cache/lyrics/state.json` is younger than 8 s, its `source` is `apple_music`, and its `state` is `playing` or `paused` | `com.apple.Music` |
| Otherwise | whatever holder MediaRemote reported — possibly nothing |

The fallback exists because holding the session is not the same as being the
player: a browser that starts a video takes Now Playing over while Apple Music
keeps playing, and reporting the browser hid the Star widget — and sent Open
Player to the wrong app — for as long as the tab lived. BTT's own
`BTTCurrentlyPlayingApp` variable proved unreliable for the switch, which is
why this resolves the holder itself.

`nowplaying-cli get-raw` is bounded at 30 × 0.05 s and then killed; it can
hang when no app holds a session. A missing `nowplaying-cli`, or an empty
answer, is not the end of it — the sampler fallback can still name the player.

### Widget State

| Condition | Returned |
| --- | --- |
| Holder is `com.apple.Music`, Music is running, player state is not stopped, track favorited | `★` |
| Same, not favorited | `☆` |
| Holder is not `com.apple.Music`, or Music is not running, or player state is stopped | `""` |

## Journey Contracts

### Toggle

**Input:** a tap while the widget is visible.

**Transformation:**

```text
resolve holder -> read favorited -> paint the opposite symbol
    -> write favorited -> return the painted symbol
```

**Output:** the widget shows the new symbol at once, and Apple Music's library
records the change a moment later.

**Properties:**

- The repaint comes first, through `update_touch_bar_widget`, because Apple
  Music can take a moment to persist the change and the widget would otherwise
  show the old symbol until its next 10 s tick.
- The script returns the symbol rather than falling off the end: an
  AppleScript widget that returns nothing shows `missing value`.
- Not idempotent by design — each tap flips the flag.
- The whole action runs asynchronously in the background
  (`Run Apple Script (async in background)`), so BTT's runner is not held for
  the round trip to Music.

### Open Player

**Input:** a long press on the Lyrics widget.

**Transformation:** `now-playing-app.sh` → `tell application id
<bundle-id>` → `activate`, `reopen`.

**Property:** nothing happens when the bundle id is empty, which is the case
when no app holds the session and the sampler has nothing fresh. The action
used to live on the Now Playing widget, whose long press now toggles the
`Music` Touch Bar group instead.

## Implementation Map

| Contract | Stable owner |
| --- | --- |
| Widget script, tap script, 10 s interval, group merge | [`bttpreset/Default.bttpreset`](../../bttpreset/Default.bttpreset) |
| Holder resolution | [`actions/now-playing-app.sh`](../../actions/now-playing-app.sh) |
| Sampler state producer | [`widgets/lyrics/cli.py`](../../widgets/lyrics/cli.py) |
| Repaint after a track change | [`actions/track-changed.sh`](../../actions/track-changed.sh) |

## Verification Map

No automated tests; both scripts live in the preset and need a live Music.

| Behaviour to verify | Focused evidence |
| --- | --- |
| Holder gate | Play a video in a browser with Music stopped; `now-playing-app.sh` prints the browser's bundle id and the widget disappears. |
| Sampler fallback | Play Music, then start a browser video; the script must still print `com.apple.Music` and the star must stay. |
| Sample ageing | Backdate `sampled_at` in `cache/lyrics/state.json` past 8 s and confirm the fallback stops. |
| Stopped player | Stop Music and confirm the widget returns `""` rather than a stale symbol. |
| Toggle latency | Tap and confirm the symbol flips before Apple Music's library reflects it. |
| No missing value | Tap while Music is stopped and confirm the widget shows nothing rather than `missing value`. |

## Known Gaps

- The widget's logic lives only in the exported preset, so it is not reviewed,
  linted, or diffed like the scripts in `widgets/`. A preset re-export can
  change it silently.
- `favorited` is Apple Music's own flag; nothing here reconciles a change made
  in Music while the widget is showing the old value, beyond the 10 s tick.
- `actions/tap-refresh.sh` writes a `.force` marker for the Star UUID that
  nothing consumes — the same residue recorded in [`LYRICS.md`](LYRICS.md).
- The Star widget and `now-playing.sh` reimplement the same holder fallback in
  two languages; they agree today by review, not by construction.

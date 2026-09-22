# Quota Widgets Specification

Indexed from [`specs/FEATURES.md`](../FEATURES.md). Lifecycle, caching,
colour interpolation, and tracing belong to
[`specs/design/WIDGET-RUNTIME.md`](WIDGET-RUNTIME.md) and are not repeated
here.

## Reconstruction Target

This document preserves what the three coding-assistant quota widgets show:
where each one's usage record comes from, how that record becomes two short
rows, which window is shown when a quota is exhausted, and how the label
colour tracks usage and the wait for a reset.

Each external source is an external boundary: `codexbar`'s provider protocol,
authentication, and JSON schema beyond the fields read below, and the
OpenCode Go usage API's own response shape.

## Entry Points

| Entry point | Trigger | Contract |
| --- | --- | --- |
| `widgets/claude-quota.sh <uuid>` | BTT widget, 600 s interval | Two rows: Claude's 5-hour and 7-day windows. |
| `widgets/codex-quota.sh <uuid>` | BTT widget, 300 s interval | Two rows: Codex's 5-hour and 7-day windows. |
| `widgets/opencode-quota.sh <uuid>` | BTT widget, 300 s interval | Two rows: OpenCode Go's 5-hour and 7-day windows, read from the plan's usage API. |
| `<widget>.sh --refresh <uuid>` | The detached refresh | Runs the widget's fetch, stores the value and the reset epoch. Never invoked by BTT directly. |
| Tap → `actions/tap-refresh.sh <own-uuid>` | User | Refreshes that widget alone. |
| After Mac wakes from sleep | BTT trigger 606 | Refreshes all three quota widgets and the Clash latency widget in one AppleScript. |

| Variable | Default | Meaning |
| --- | --- | --- |
| `CLAUDE_QUOTA_MAX_AGE`, `CODEX_QUOTA_MAX_AGE`, `OPENCODE_QUOTA_MAX_AGE` | `300` | How old a stored value may be before a refresh is started. |
| `OPENCODE_QUOTA_API_KEY` | the key `opencode auth login` stored | Credential for the OpenCode Go usage API. |
| `OPENCODE_QUOTA_AUTH_FILE` | `~/.local/share/opencode/auth.json` | Where that key is read from when the variable is unset. |
| `OPENCODE_QUOTA_TIMEOUT` | `15` | Seconds the usage request may take. |
| `OPENCODE_QUOTA_USAGE_URL` | `https://opencode.ai/zen/go/v1/usage` | The usage endpoint. |
| `BTT_LOG_DIR` | `$BTT_REPO_DIR/logs` | The active fetch's stderr goes to `logs/btt-codexbar.log`. |

The BTT refresh interval and the cache age are independent: the widget may
tick every 300 s and still publish a value it decides is fresh.

## Feature Tree

```text
Quota widgets
├── Fetch one usage record, off the widget path
│   ├── claude, codex: codexbar, in codexbar's own record shape
│   │   ├── claude: codexbar --provider claude --source auto --format json
│   │   └── codex:  codexbar usage --provider codex --source auto --format json
│   ├── opencode: the plan's own authenticated usage API
│   │   ├── GET https://opencode.ai/zen/go/v1/usage, bearer key from
│   │   │   OPENCODE_QUOTA_API_KEY or ~/.local/share/opencode/auth.json
│   │   └── Normalise {usage: {rolling, weekly, monthly}} into codexbar's
│   │       record shape, so the rows and reset persistence below are shared
│   └── Append the fetch's stderr to logs/btt-codexbar.log
├── Reduce one usage record to two rows
│   ├── Round usedPercent, or "—" when absent
│   ├── Express time to reset as d / h / m / <1m
│   └── The 5-hour window first, the 7-day window second, in every widget
├── Persist the exact reset moment
│   ├── <name>.quota-reset for the primary window
│   └── <name>-secondary.quota-reset for the secondary window
├── Colour the label by how much is left
│   ├── Below 100%: brighter the more quota remains
│   └── At 100%: brighter the closer the reset is
└── Report a broken dependency in the row itself
    ├── NO CODEXBAR / NO JQ / HOME ERR
    └── EMPTY JSON / ERR
```

## Decision Trees

### Value Composition

| Widget | Windows read | Row 1 | Row 2 |
| --- | --- | --- | --- |
| Claude | `usage.primary` (5 h), `usage.secondary` (7 d) | primary (5 h) | secondary (7 d) |
| Codex | first non-null of `usage.primary`, `usage.secondary`, `usage.tertiary`, then `usage.secondary` (7 d) | primary window | secondary (7 d) |
| OpenCode | `usage.primary` (5 h), `usage.secondary` (7 d); primary falls back to secondary. The fetch maps the API's `rolling`, `weekly`, and `monthly` windows onto primary, secondary, and tertiary. | primary (5 h) | secondary (7 d) |

Every row packs the used percentage and the time to reset. Real cached
values:

```console
$ cat cache/claude-quota.value
15% 3h
27% 4d
$ cat cache/codex-quota.value
0% 4h
0% 6d
$ cat cache/opencode-quota.value
3% 4h
10% 5d
```

`usedPercent` is rounded to a whole number, or rendered `—` when the field is
null. Time to reset is derived from `resetsAt` (ISO 8601) as
`max(resetsAt - now, 0)` and formatted as `<n>d` above a day, `<n>h` above an
hour, `<n>m` above a minute, and `<1m` below that.

### Failure Values

Every failure is a value, not an error exit: the widget always publishes
something, and the runtime traces the outcome as `error`.

| Condition | Published |
| --- | --- |
| `cd "$HOME"` fails | `HOME ERR` |
| `jq` missing or not executable | `NO JQ` |
| `codexbar` missing or not executable | `NO CODEXBAR` |
| `codexbar`, or the usage endpoint, answered with nothing | `EMPTY JSON` |
| No OpenCode Go key: neither `OPENCODE_QUOTA_API_KEY` nor a key in the auth file | `NO KEY` |
| The usage endpoint rejected the key (`401`/`403`) | `AUTH ERR` |
| `jq` failed, or produced no text | `ERR` |

A fetch reports its own dependency's state, and `quota_compute_value`
publishes it verbatim: the fetch prints the token and returns non-zero, so a
failed fetch can never be read as a usage record. `NO KEY`, `AUTH ERR`, and
`EMPTY JSON` on the OpenCode widget are therefore the whole diagnosis — the
row itself says which link of key, endpoint, or parse broke.

`codexbar` is resolved through `command -v` by the default fetch alone, so a
widget that fetches its own usage never reports `NO CODEXBAR`.

### Colour By Usage And Reset Progress

`btt_quota_color` reads the value it is about to publish. Below 100% the
colour tracks usage — the more quota remains, the brighter the label; at 100%
it tracks the wait for a reset:

```text
used% < 100          -> progress = 100 - used%
used% == 100         -> progress = (cycle - minutes to reset) * 100 / cycle
no numeric percent   -> BTT_WIDGET_COLOR, no interpolation
```

`progress` then interpolates from `BTT_WIDGET_QUOTA_DIM_COLOR` (205,205,205)
at 0 to `BTT_WIDGET_COLOR` (255,255,255) at 100. So a fresh quota is white,
a nearly spent one is grey, and an exhausted one brightens again as its reset
approaches.

| Widget | `BTT_WIDGET_QUOTA_RESET_CYCLE_MINUTES` | Secondary cycle |
| --- | --- | --- |
| Claude | `300` (5 h) | `10080` (7 d) |
| Codex | `300` (5 h) | `10080` (7 d) |
| OpenCode | `300` (5 h) | `10080` (7 d) |

Any exhausted secondary window takes over the colour: with a secondary
cycle configured and a second row reading `>= 100%`, the colour is computed
from that row, that cycle, and the `<name>-secondary.quota-reset` file.

Minutes to reset come from the stored epoch when
`cache/<name>.quota-reset` holds an integer; otherwise they are parsed back
out of the row's own label (`4d`, `3h`, `12m`, `<1m`). The stored epoch is
authoritative because the label was rendered when the value was computed and
ages with it. Remaining minutes are clamped to `[0, cycle]`.

## Journey Contracts

### One Refresh

**Input:** `--refresh` mode, no arguments beyond the widget UUID.

**Transformation:**

```text
fetch -> jq: resetsAt -> cache/<name>.quota-reset
      -> jq: two rows  -> cache/<name>.value
```

**Outputs:** the value file, up to two reset files, and one `refresh` trace
row carrying `value=<what was stored>`.

**Properties:** bounded only by `BTT_WIDGET_REFRESH_MAX_RUN` (180 s), which is
why it must never run on the widget path — codexbar's Claude lookup takes the
best part of a minute, the OpenCode Go key lookup is a file read on the same
path, and BTT runs every shell widget through one XPC service.

**Reset persistence:** `btt_quota_reset_put <name> <epoch>` writes the file
atomically; an empty epoch removes it, so a window whose `resetsAt` went away
does not leave a stale deadline behind.

## Implementation Map

| Contract | Stable owner |
| --- | --- |
| Claude rows, both windows | [`widgets/claude-quota.sh`](../../widgets/claude-quota.sh) |
| Codex rows, both windows, primary-window fallback chain | [`widgets/codex-quota.sh`](../../widgets/codex-quota.sh) |
| OpenCode rows, both windows, weekly repeat when the primary is absent | [`widgets/opencode-quota.sh`](../../widgets/opencode-quota.sh) |
| OpenCode Go key resolution, request, and record normalisation | [`widgets/opencode-quota.sh`](../../widgets/opencode-quota.sh) |
| The fetch hook, the codexbar fetch, jq resolution | [`widgets/lib/quota-widget.sh`](../../widgets/lib/quota-widget.sh) |
| Cache, lock, trace, publish, quota colour | [`widgets/lib/btt-widget.sh`](../../widgets/lib/btt-widget.sh) |
| Intervals, tap actions, wake trigger | [`bttpreset/Default.bttpreset`](../../bttpreset/Default.bttpreset) |

## Verification Map

No automated tests; each widget's external source is the only source of truth
for its own JSON shape.

| Behaviour to verify | Focused evidence |
| --- | --- |
| Row shape | Run `widgets/codex-quota.sh --refresh` and confirm `cache/codex-quota.value` holds two rows, the 5-hour window over the 7-day one. |
| OpenCode rows against the plan | Run `widgets/opencode-quota.sh --refresh` and confirm `cache/opencode-quota.value` matches the percentages and windows the usage API reports, `curl -sS -H "Authorization: Bearer <key>" https://opencode.ai/zen/go/v1/usage`. |
| OpenCode reset epochs | Confirm `cache/opencode-quota.quota-reset` and `cache/opencode-quota-secondary.quota-reset` hold epochs matching the API's `rolling.resetsAt` and `weekly.resetsAt` (fractional seconds dropped, or jq's `fromdateiso8601` rejects them). |
| OpenCode key failures | Run `--refresh` with `OPENCODE_QUOTA_AUTH_FILE=/nonexistent` (row `NO KEY`) and with `OPENCODE_QUOTA_API_KEY=go_bogus` (`AUTH ERR`); confirm the trace records `error` for both. |
| OpenCode unreachable endpoint | Run `--refresh` with `OPENCODE_QUOTA_USAGE_URL` pointed at a closed port and confirm the row reads `EMPTY JSON`. |
| Colour at a full secondary | Feed `jq` a record with `secondary.usedPercent = 100` and confirm the colour is computed from the secondary reset and the 10080-minute cycle. |
| Missing figures | Feed `usedPercent: null` and confirm the row reads `—`. |
| Duration bands | Feed `resetsAt` at +90000 s, +5000 s, +100 s, +10 s and confirm `1d`, `1h`, `1m`, `<1m`. |
| Dependency failures | Run with `PATH` stripped of `codexbar` and of `jq`; confirm `NO CODEXBAR` and `NO JQ` reach the row. |
| Reset persistence | Confirm `cache/<name>.quota-reset` holds an integer epoch after a refresh, and is removed when `resetsAt` is null. |
| Colour at 100% | Publish `100% 2h` with the stored epoch two hours out and confirm the colour sits between the dim and normal colours, not at either end. |
| Colour fallback | Remove the reset file and confirm the label suffix alone still produces the same band. |

## Known Gaps

- `codexbar`'s JSON schema is unversioned here: a renamed field silently
  becomes `ERR` in the row with no other signal than
  `logs/btt-codexbar.log`.
- `codexbar` cannot see OpenCode Go plan usage here. Its `local` strategy
  derives the windows from the OpenCode CLI's own message history, so usage
  spent through another client (the VS Code provider, `opencode serve`) reads
  as 0% in every window, and its `web` strategy needs an `opencode.ai` session
  cookie. Both are why this widget fetches the usage API itself; re-check that
  assumption on any `codexbar` upgrade before simplifying the fetch back.
- The usage API's response fields are read leniently (any window whose
  `percent` is not a number becomes null) but nothing pins its schema either:
  a renamed window degrades to the weekly repeat in row one, and a renamed
  `percent` to `—`.
- Claude uses `codexbar --provider …` while Codex uses `codexbar usage
  --provider …`. Whether the bare form is an alias or a different code path in
  `codexbar` is unverified.
- Codex falls back through primary, secondary, and tertiary without saying
  which window it ended up showing, and OpenCode repeats the weekly window
  in the first row when its five-hour one is absent: both draw a different
  quota under the same label without saying which.
- The colour interpolation is linear in remaining time, which reads as
  "nearly reset" long before it is; no measurement backs the choice of curve.

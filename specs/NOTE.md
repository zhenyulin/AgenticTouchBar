# Notes

Durable learnings promoted by `repo-learn`. Behaviour facts live in
`specs/design/`; correction patterns and lessons live here.

## BTT layout keys (empirical, 2026-08-12)

`BTTTouchBarIconTextOffset` and `BTTTouchBarItemPadding` do **not** affect
the gap between a script widget's icon and its text — tested at 5, 2, 8 and
-18 with no visible change. Those keys apply to button-style items; BTT lays
out script-widget icon + text with its own internal spacing, and the widget
JSON payload has no spacing key.

The levers that do move the gap:

- the icon image's own transparent margins (e.g. the play triangle's canvas
  in `assets/now-playing-play.svg`, trimmed 2026-08-12), and
- `BTTTouchBarItemIconWidth` — the text starts after the icon slot, so a
  smaller slot width pulls the text left (Now Playing: 30 -> 26, 2026-08-12).

## Preset files

- `bttpreset/backup.bttpreset` is **reserved for manual import/export
  only — never edit it** (no scripted or agent edits). It is a hand-managed
  fallback snapshot; BTT may overwrite it at any time on the user's machine.
- `bttpreset/Default.bttpreset` is the canonical preset source. Preset
  changes land there; the backup is refreshed by manual export only.

## BTT Date/Time widget format tokens are TR35 (2026-08-15)

`BTTTouchBarDateFormat` uses Unicode TR35 date-field symbols: uppercase
`MM` is the **month**, lowercase `mm` is **minutes**. The time widget's
`"HH:MM"` (from the 2026-08-13 date/time split) rendered the month number
as minutes — the clock looked like `03:08` all August and could never
reach the real minute, reading as "lagged behind". Fixed to `"HH:mm"`.
The date widget's `"MM-dd"` was always correct, which is what makes the
pair asymmetric and the bug easy to miss. When editing widget formats,
verify token case against the date widget's known-good sibling.

## Freeze-guard arc is closed (2026-08-13)

The freeze guard and all of its observability were **removed**, not disabled:

- BTT was upgraded to V6 (2026-08-12); its fixed refresh thread resolved the
  Touch Bar freeze root cause the guard compensated for.
- Deleted: `actions/btt-freeze-guard.sh`, `actions/freeze-catch.sh`,
  `actions/hid-state.c`, `actions/weather-state.sh`, `STATS.md`,
  restart.tsv/freeze-guard.log markers, latency-history-driven restart,
  the lyrics viewport subsystem.
- Do **not** re-introduce freeze-guard machinery, forced restart schedules,
  or hidden-widget freeze probes. The only restart surfaces are the manual
  tap (`actions/tap-restart.sh`) and quit (`actions/btt-quit.sh`).
- Pattern to repeat: once an upstream fix lands, delete the compensating
  machinery and its observability in one pass, not just the trigger.

## Lyrics cache robustness (2026-08-10..13)

- Cache reads must tolerate malformed entries: a single stray non-record
  JSON line in `cache/lyrics/` killed the whole render tick (fixed
  2026-08-12 by defensive parsing).
- The Apple Music lyrics cache is deliberately the **last** provider —
  probing it eagerly burdens the Apple Music player.
- Cache lookups are gated on Apple Music being the active player; when QQ
  Music or nothing plays, the cache is skipped entirely.
- LRC provider priority: QQ Music above NetEase.

## Repo root from env, never hardcoded

Scripts derive the repo root from `BTT_REPO_DIR`
(`${BTT_REPO_DIR:-$HOME/Documents/BTT}`) — never hardcode
`~/Documents/BTT`. Fixed across `actions/` and `widgets/` on 2026-08-12.

## Layout tuning is empirical

The user tunes Touch Bar layout constants (paddings, width budgets, font
sizes) one small numeric change at a time, verified visually in the bar.
When asked for a layout adjustment, change exactly the requested number —
do not re-derive neighbouring constants or introduce estimation machinery
beyond what is asked.

## Apple Weather Shortcut bridge parked (2026-09-18)

The Apple Weather Shortcut route in `widgets/weather.sh`
(`BTT_WEATHER_SHORTCUT`) was **removed**, not switched off by default: a
`shortcuts run` that fails raises a Shortcuts alert, and every refresh of the
row runs it, so a Mac with no location fix (the 2026-09-16 wedge) turns the
screen into an alert queue while QWeather answers normally underneath. A
Shortcut cannot fail quietly for this caller, so do not re-enable the route
without a fix for the alert behaviour. Git history has the removed
`fetch_shortcut_weather`, `shortcut_icon` and the JSON contract.

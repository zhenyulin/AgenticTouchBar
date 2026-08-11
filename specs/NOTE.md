# Notes

## Script-widget icon/text spacing (empirical, 2026-08-12)

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

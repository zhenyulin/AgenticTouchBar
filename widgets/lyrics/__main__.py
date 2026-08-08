#!/usr/bin/env python3
"""BetterTouchTool Touch Bar lyrics widget for Apple Music.

Apple Music supplies track metadata and playback position through AppleScript.
A token-free Chinese lyrics aggregator (LrcAPI) and LRCLIB supply synchronized LRC lyrics, which are cached locally.

The widget path never blocks. BetterTouchTool runs widget scripts one at a
time and its refresh_widget command waits for them, so a slow run freezes
every other widget's tap-refresh too. Neither of the two slow things happens
inline: a cache miss starts a background lyrics fetch, and Apple Music is
sampled by a detached helper whose last sample the widget reads from disk.

This package is run with `python3 -m lyrics`, not executed by path -- see
widgets/now-playing-lyrics.sh, which is the thing BetterTouchTool and the
shell actions actually invoke, and which puts `widgets/` on PYTHONPATH so
`lyrics` resolves. `-m` is what gives this file real package context, so the
modules next to it can use ordinary relative imports.
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""BetterTouchTool Touch Bar lyrics widget for Apple Music.

Apple Music supplies track metadata and playback position through AppleScript.
A token-free Chinese lyrics aggregator (LrcAPI) and LRCLIB supply synchronized LRC lyrics, which are cached locally.

The widget path never blocks. BetterTouchTool runs widget scripts one at a
time and its refresh_widget command waits for them, so a slow run freezes
every other widget's tap-refresh too. Neither of the two slow things happens
inline: a cache miss starts a background lyrics fetch, and Apple Music is
sampled by a detached helper whose last sample the widget reads from disk.

This file is the thin entry point BetterTouchTool and the shell scripts
invoke directly, by path -- see actions/track-changed.sh and
bttpreset/Default.bttpreset. The implementation lives in lyrics_widget/,
next to this file, split into one module per concern because it outgrew a
single 2000+ line file.
"""

from __future__ import annotations

from lyrics_widget.cli import main

if __name__ == "__main__":
    raise SystemExit(main())

"""Shared helpers for the lyrics tests.

`lyrics.config` reads its environment once, at import time, so every path it
exposes is fixed by whatever the environment held when the first test imported
it. Importing this module first pins those paths inside a per-run temporary
directory, which is what keeps the tests off the live cache in `cache/lyrics`
and out of `logs/`.

Run them with `tests/run.sh`, which sets the same variables before Python
starts -- this module is the belt to that script's braces, so a test run
straight from an editor is still isolated.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_SANDBOX = Path(tempfile.mkdtemp(prefix="btt-lyrics-tests-"))

# Set before `lyrics.config` is imported anywhere below.
os.environ.setdefault("BTT_LYRICS_CACHE_DIR", str(_SANDBOX / "cache"))
os.environ.setdefault("BTT_LOG_DIR", str(_SANDBOX / "logs"))
os.environ.setdefault("BTT_LYRICS_LOCAL_DIR", str(_SANDBOX / "local-lrc"))
# Tracing appends to a file on every call; the tests do not need the evidence.
os.environ.setdefault("BTT_LYRICS_TRACE", "0")

for _directory in ("cache", "logs", "local-lrc"):
    (_SANDBOX / _directory).mkdir(parents=True, exist_ok=True)

SANDBOX = _SANDBOX


def track(**overrides: object) -> dict[str, object]:
    """A sampled-track dict with sensible defaults, for matching and layout.

    Mirrors the shape `lyrics.sources.apple_music.current_track` returns; tests
    override only the fields they care about.
    """
    base: dict[str, object] = {
        "state": "playing",
        "title": "Yellow",
        "artist": "Coldplay",
        "album": "Parachutes",
        "genre": "Alternative",
        "duration": 269.0,
        "position": 30.0,
        "source": "apple_music",
    }
    base.update(overrides)
    return base


def candidate(**overrides: object) -> dict[str, object]:
    """A provider record, as the provider adapters build them."""
    base: dict[str, object] = {
        "id": "test:1",
        "source": "test",
        "trackName": "Yellow",
        "artistName": "Coldplay",
        "albumName": "Parachutes",
        "duration": 269.0,
        "instrumental": False,
        "plainLyrics": None,
        "syncedLyrics": "",
    }
    base.update(overrides)
    return base

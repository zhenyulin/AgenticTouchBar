"""Process locks and the detached-helper spawner they guard."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from . import config
from .cache import lock_path


def acquire_lock(path: Path, max_age_seconds: float) -> bool:
    """Take an exclusive lock file, dropping one left behind by a dead run."""
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        if path.exists() and time.time() - path.stat().st_mtime > max_age_seconds:
            path.unlink()
    except OSError:
        pass

    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        return True
    except FileExistsError:
        return False


def clear_lock(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def spawn_helper(arguments: list[str]) -> None:
    subprocess.Popen(
        [sys.executable, str(_entry_point()), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )


def _entry_point() -> Path:
    """The top-level script BetterTouchTool invokes, so a detached helper
    re-enters through the same path rather than importing the package."""
    return Path(__file__).resolve().parent.with_name("now_playing_lyrics.py")


def acquire_widget_lock() -> bool:
    return acquire_lock(config.WIDGET_LOCK_PATH, config.WIDGET_LOCK_MAX_AGE_SECONDS)


def release_widget_lock() -> None:
    try:
        if config.WIDGET_LOCK_PATH.read_text(encoding="utf-8") == str(os.getpid()):
            config.WIDGET_LOCK_PATH.unlink()
    except (FileNotFoundError, OSError):
        pass


def fetch_waiting_seconds(key: str) -> float:
    """How long the in-flight fetch for this track has been running."""
    try:
        return max(time.time() - lock_path(key).stat().st_mtime, 0.0)
    except OSError:
        return float("inf")

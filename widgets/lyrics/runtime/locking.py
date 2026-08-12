"""Process locks and the detached-helper spawner they guard."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from .. import config
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
    """Launch a detached `python3 -m lyrics <arguments>`.

    This package has no standalone entry file: `__main__.py` relies on `-m`
    for its package context (relative imports), so re-entering it means
    putting the package's parent directory on PYTHONPATH, the same way
    widgets/now-playing-lyrics.sh does, rather than pointing at a path.
    """
    env = dict(os.environ)
    package_root = str(_package_root())
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        package_root + os.pathsep + existing if existing else package_root
    )
    subprocess.Popen(
        [sys.executable, "-m", "lyrics", *arguments],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )


def _package_root() -> Path:
    """The directory `lyrics` needs on PYTHONPATH to be importable by name.

    This module lives in `lyrics/runtime/`, so the directory holding the
    package is two levels up -- keep this in step if the module ever moves.
    """
    return Path(__file__).resolve().parents[2]


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

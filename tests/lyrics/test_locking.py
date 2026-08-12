"""Process locks, and the package root the detached helper is launched from."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.lyrics.support import SANDBOX  # noqa: F401  (pins config paths first)

from lyrics import config
from lyrics.runtime.locking import (
    _package_root,
    acquire_lock,
    acquire_widget_lock,
    clear_lock,
    fetch_waiting_seconds,
    release_widget_lock,
)


class PackageRoot(unittest.TestCase):
    def test_points_at_the_directory_holding_the_lyrics_package(self):
        # spawn_helper puts this on PYTHONPATH and runs `python3 -m lyrics`,
        # so if it is wrong every detached fetch and sample dies on import.
        # It is derived by counting parents, which makes it break silently
        # whenever the module moves between subpackages -- hence this test.
        root = _package_root()
        self.assertEqual(root.name, "widgets")
        self.assertTrue((root / "lyrics" / "__init__.py").is_file())
        self.assertTrue((root / "lyrics" / "__main__.py").is_file())


class LockDirTestCase(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        patcher = patch.object(config, "CACHE_DIR", self.cache_dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lock = self.cache_dir / "test.lock"


class AcquireLock(LockDirTestCase):
    def test_an_absent_lock_is_taken(self):
        self.assertTrue(acquire_lock(self.lock, 60.0))
        self.assertTrue(self.lock.exists())

    def test_a_held_lock_is_refused(self):
        # This is what keeps a run of quick widget ticks from piling up
        # osascript processes behind an unresponsive Music.app.
        self.assertTrue(acquire_lock(self.lock, 60.0))
        self.assertFalse(acquire_lock(self.lock, 60.0))

    def test_a_stale_lock_is_stolen(self):
        # A lock older than the work could possibly take belongs to a run that
        # died; nothing else would still be holding it.
        self.assertTrue(acquire_lock(self.lock, 60.0))
        old = time.time() - 120
        os.utime(self.lock, (old, old))
        self.assertTrue(acquire_lock(self.lock, 60.0))

    def test_a_fresh_lock_is_not_stolen_at_the_boundary(self):
        self.assertTrue(acquire_lock(self.lock, 60.0))
        recent = time.time() - 5
        os.utime(self.lock, (recent, recent))
        self.assertFalse(acquire_lock(self.lock, 60.0))

    def test_the_holder_records_its_pid(self):
        acquire_lock(self.lock, 60.0)
        self.assertEqual(self.lock.read_text(encoding="utf-8"), str(os.getpid()))

    def test_the_cache_directory_is_created_if_missing(self):
        nested = self.cache_dir / "deeper"
        with patch.object(config, "CACHE_DIR", nested):
            self.assertTrue(acquire_lock(nested / "a.lock", 60.0))


class ClearLock(LockDirTestCase):
    def test_releases_a_held_lock(self):
        acquire_lock(self.lock, 60.0)
        clear_lock(self.lock)
        self.assertFalse(self.lock.exists())
        self.assertTrue(acquire_lock(self.lock, 60.0))

    def test_clearing_a_lock_that_is_not_there_is_harmless(self):
        # The failure paths call this without knowing whether they got it.
        clear_lock(self.lock)


class WidgetLock(LockDirTestCase):
    def setUp(self):
        super().setUp()
        patcher = patch.object(
            config, "WIDGET_LOCK_PATH", self.cache_dir / "widget.lock"
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_acquire_then_release(self):
        self.assertTrue(acquire_widget_lock())
        self.assertFalse(acquire_widget_lock())
        release_widget_lock()
        self.assertTrue(acquire_widget_lock())

    def test_release_leaves_another_process_lock_alone(self):
        # Releasing a lock this process never took would let two widget runs
        # render at once, which is exactly what the lock exists to prevent.
        config.WIDGET_LOCK_PATH.write_text("999999", encoding="utf-8")
        release_widget_lock()
        self.assertTrue(config.WIDGET_LOCK_PATH.exists())

    def test_releasing_a_missing_lock_is_harmless(self):
        release_widget_lock()


class FetchWaitingSeconds(LockDirTestCase):
    def test_no_fetch_in_flight_reads_as_infinite(self):
        # Callers compare against a patience threshold; "no lock" must never
        # look like "just started".
        self.assertEqual(fetch_waiting_seconds("nokey"), float("inf"))

    def test_a_fresh_lock_has_barely_been_waiting(self):
        acquire_lock(self.cache_dir / "k.lock", 60.0)
        self.assertLess(fetch_waiting_seconds("k"), 1.0)

    def test_an_older_lock_reports_its_age(self):
        path = self.cache_dir / "k.lock"
        acquire_lock(path, 600.0)
        old = time.time() - 30
        os.utime(path, (old, old))
        self.assertGreaterEqual(fetch_waiting_seconds("k"), 29.0)


if __name__ == "__main__":
    unittest.main()

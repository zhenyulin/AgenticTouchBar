"""The on-disk lyrics cache, and salvaging a record across cache keys."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.lyrics.support import track  # noqa: F401  (pins config paths first)

from lyrics import config
from lyrics.runtime.cache import (
    atomic_write_json,
    cache_path,
    lock_path,
    read_cache,
    read_compatible_cache,
)


def ok_record(**overrides):
    """A stored `ok` payload, as background_fetch writes one."""
    record = {
        "trackName": "Yellow",
        "artistName": "Coldplay",
        "albumName": "Parachutes",
        "duration": 269.0,
        "source": "lrclib",
        "syncedLyrics": "[00:01.00]line",
    }
    record.update(overrides.pop("record", {}))
    payload = {
        "status": "ok",
        "cache_version": config.CACHE_KEY_VERSION,
        "fetched_at": 0.0,
        "record": record,
    }
    payload.update(overrides)
    return payload


class CacheDirTestCase(unittest.TestCase):
    """Point CACHE_DIR at a fresh directory for each test."""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.cache_dir = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        patcher = patch.object(config, "CACHE_DIR", self.cache_dir)
        patcher.start()
        self.addCleanup(patcher.stop)


class Paths(CacheDirTestCase):
    def test_cache_and_lock_paths_are_derived_from_the_key(self):
        self.assertEqual(cache_path("abc"), self.cache_dir / "abc.json")
        self.assertEqual(lock_path("abc"), self.cache_dir / "abc.lock")

    def test_different_keys_do_not_collide(self):
        self.assertNotEqual(cache_path("abc"), cache_path("abd"))


class ReadWrite(CacheDirTestCase):
    def test_round_trips_a_payload(self):
        atomic_write_json(cache_path("k"), ok_record())
        self.assertEqual(read_cache("k"), ok_record())

    def test_writes_are_atomic_leaving_no_temporary_behind(self):
        # A half-written file read by the next tick would blank the widget.
        atomic_write_json(cache_path("k"), ok_record())
        self.assertEqual([p.name for p in self.cache_dir.iterdir()], ["k.json"])

    def test_non_ascii_survives_the_round_trip(self):
        payload = ok_record(record={"trackName": "寶貝", "artistName": "張懸"})
        atomic_write_json(cache_path("k"), payload)
        self.assertEqual(read_cache("k")["record"]["trackName"], "寶貝")

    def test_a_missing_file_reads_as_none(self):
        self.assertIsNone(read_cache("absent"))

    def test_a_corrupt_file_reads_as_none_and_is_removed(self):
        # A truncated write must not wedge the widget forever; dropping the
        # file lets the next tick re-fetch.
        path = cache_path("broken")
        path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(read_cache("broken"))
        self.assertFalse(path.exists())


class ReadCompatibleCache(CacheDirTestCase):
    def test_an_existing_record_is_returned_untouched(self):
        atomic_write_json(cache_path("k"), ok_record())
        self.assertEqual(read_compatible_cache("k", track()), ok_record())

    def test_a_not_found_record_is_salvaged_from_a_matching_key(self):
        # The cache key includes the duration, so the same recording sampled
        # with a slightly different length lands on a new key. Rather than
        # re-fetching, the earlier record is copied across.
        atomic_write_json(cache_path("old"), ok_record())
        atomic_write_json(
            cache_path("new"),
            {"status": "not_found", "cache_version": config.CACHE_KEY_VERSION},
        )
        salvaged = read_compatible_cache("new", track())
        self.assertEqual(salvaged["status"], "ok")
        # ...and is written under the new key, so the next tick reads it directly.
        self.assertEqual(read_cache("new")["status"], "ok")

    def test_a_missing_record_is_salvaged_too(self):
        atomic_write_json(cache_path("old"), ok_record())
        self.assertEqual(read_compatible_cache("new", track())["status"], "ok")

    def test_a_record_from_an_older_cache_version_is_never_salvaged(self):
        # It was produced by provider behaviour that may no longer match this
        # track, so resurrecting it would reinstate a stale mismatch.
        atomic_write_json(
            cache_path("old"), ok_record(cache_version=config.CACHE_KEY_VERSION - 1)
        )
        self.assertIsNone(read_compatible_cache("new", track()))

    def test_a_record_whose_duration_is_too_far_off_is_not_salvaged(self):
        atomic_write_json(cache_path("old"), ok_record(record={"duration": 300.0}))
        self.assertIsNone(read_compatible_cache("new", track()))

    def test_a_record_for_another_track_is_not_salvaged(self):
        atomic_write_json(cache_path("old"), ok_record(record={"trackName": "Clocks"}))
        self.assertIsNone(read_compatible_cache("new", track()))

    def test_a_close_enough_duration_is_salvaged(self):
        # Two seconds is the tolerance; TTML and AppleScript disagree by about
        # that much on the same recording.
        atomic_write_json(cache_path("old"), ok_record(record={"duration": 267.5}))
        self.assertEqual(read_compatible_cache("new", track())["status"], "ok")

    def test_an_apple_cache_record_is_dropped_rather_than_salvaged(self):
        # Apple-cache records carry the metadata of whatever TTML the duration
        # match picked, so they cannot be trusted under a different key.
        old = cache_path("old")
        atomic_write_json(old, ok_record(record={"source": "apple-cache"}))
        self.assertIsNone(read_compatible_cache("new", track()))
        self.assertFalse(old.exists())

    def test_an_incomplete_track_skips_the_salvage_scan(self):
        # Without a title, artist and duration there is nothing safe to match
        # on, so the stored answer stands as-is.
        atomic_write_json(cache_path("old"), ok_record())
        self.assertIsNone(read_compatible_cache("new", track(artist="")))

    def test_unreadable_neighbours_do_not_break_the_scan(self):
        (self.cache_dir / "junk.json").write_text("{not json", encoding="utf-8")
        atomic_write_json(cache_path("old"), ok_record())
        self.assertEqual(read_compatible_cache("new", track())["status"], "ok")

    def test_a_non_dict_neighbour_is_ignored(self):
        # REGRESSION: the sampler's own state.json and viewport.json live in
        # this directory too, and a truncated or hand-edited file can be any
        # JSON shape. A bare list or null used to reach .get() and raise
        # AttributeError, which the scan's except clause did not catch -- so a
        # single stray file killed every render tick.
        (self.cache_dir / "list.json").write_text(json.dumps([1, 2]), encoding="utf-8")
        (self.cache_dir / "null.json").write_text("null", encoding="utf-8")
        (self.cache_dir / "text.json").write_text('"a string"', encoding="utf-8")
        atomic_write_json(cache_path("old"), ok_record())
        self.assertEqual(read_compatible_cache("new", track())["status"], "ok")

    def test_a_neighbour_with_a_non_dict_record_is_ignored(self):
        (self.cache_dir / "odd.json").write_text(
            json.dumps({"status": "ok", "record": "not a dict"}), encoding="utf-8"
        )
        atomic_write_json(cache_path("old"), ok_record())
        self.assertEqual(read_compatible_cache("new", track())["status"], "ok")


if __name__ == "__main__":
    unittest.main()

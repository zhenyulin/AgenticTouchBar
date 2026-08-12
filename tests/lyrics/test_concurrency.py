"""The fetch budget, and running provider queries in parallel."""

from __future__ import annotations

import time
import unittest

from tests.lyrics.support import SANDBOX  # noqa: F401  (pins config paths first)

from lyrics.runtime.concurrency import (
    fetch_seconds_remaining,
    map_concurrently,
    set_fetch_deadline,
)


class FetchDeadline(unittest.TestCase):
    def tearDown(self):
        # The deadline is module state; a leaked one would silently starve
        # every later test's provider calls.
        set_fetch_deadline(None)

    def test_outside_a_fetch_the_budget_is_unbounded(self):
        set_fetch_deadline(None)
        self.assertEqual(fetch_seconds_remaining(), float("inf"))

    def test_inside_a_fetch_the_budget_is_what_is_left(self):
        set_fetch_deadline(time.monotonic() + 10.0)
        remaining = fetch_seconds_remaining()
        self.assertGreater(remaining, 9.0)
        self.assertLessEqual(remaining, 10.0)

    def test_a_passed_deadline_reports_a_negative_budget(self):
        # Providers compare the remaining time against their own timeout, so
        # "already over" has to read as less than any of them, not as zero.
        set_fetch_deadline(time.monotonic() - 5.0)
        self.assertLess(fetch_seconds_remaining(), 0.0)


class MapConcurrently(unittest.TestCase):
    def test_no_items_is_no_work(self):
        self.assertEqual(map_concurrently(lambda item: item, []), [])

    def test_a_single_item_is_run_inline(self):
        self.assertEqual(map_concurrently(lambda item: item * 2, [3]), [(6, None)])

    def test_results_keep_the_input_order(self):
        # Callers zip these back against the queries that produced them, so
        # completion order must not leak into the result order.
        def slow_for_the_first(item):
            if item == 1:
                time.sleep(0.05)
            return item * 10

        self.assertEqual(
            map_concurrently(slow_for_the_first, [1, 2, 3]),
            [(10, None), (20, None), (30, None)],
        )

    def test_a_failing_item_returns_its_exception_instead_of_raising(self):
        # One dead endpoint must not lose the answers from the others.
        def fail_on_two(item):
            if item == 2:
                raise RuntimeError("boom")
            return item

        results = map_concurrently(fail_on_two, [1, 2, 3])
        self.assertEqual(results[0], (1, None))
        self.assertIsNone(results[1][0])
        self.assertIsInstance(results[1][1], RuntimeError)
        self.assertEqual(results[2], (3, None))

    def test_a_single_failing_item_is_reported_the_same_way(self):
        # The one-item fast path is a separate branch and must agree.
        def explode(_item):
            raise ValueError("nope")

        (value, error), = map_concurrently(explode, [1])
        self.assertIsNone(value)
        self.assertIsInstance(error, ValueError)


if __name__ == "__main__":
    unittest.main()

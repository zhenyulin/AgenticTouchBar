"""Running provider queries in parallel, within the current fetch's budget.

Kept apart from fetch.py: providers need fetch_seconds_remaining to decide
whether another query is still worth sending, but fetch.py is what starts
the providers running, so sharing this state through fetch.py itself would
make the two import each other.
"""

from __future__ import annotations

import time
from typing import Any

_FETCH_DEADLINE: float | None = None


def set_fetch_deadline(deadline: float | None) -> None:
    global _FETCH_DEADLINE
    _FETCH_DEADLINE = deadline


def fetch_seconds_remaining() -> float:
    """Seconds left in the current background fetch, or infinity outside one."""
    if _FETCH_DEADLINE is None:
        return float("inf")
    return _FETCH_DEADLINE - time.monotonic()


def map_concurrently(
    function: Any, items: list[Any]
) -> list[tuple[Any, Exception | None]]:
    """Apply function to every item at once, keeping the input order.

    Results come back as (value, error) pairs, so one failing query cannot
    discard the results of the others, and ranking still sees the queries in
    the order they were composed.
    """
    from concurrent.futures import ThreadPoolExecutor

    from . import config

    if not items:
        return []
    if len(items) == 1:
        try:
            return [(function(items[0]), None)]
        except Exception as exc:
            return [(None, exc)]

    with ThreadPoolExecutor(max_workers=min(len(items), config.FETCH_WORKERS)) as pool:
        futures = [pool.submit(function, item) for item in items]
        collected: list[tuple[Any, Exception | None]] = []
        for future in futures:
            try:
                collected.append((future.result(), None))
            except Exception as exc:
                collected.append((None, exc))
        return collected

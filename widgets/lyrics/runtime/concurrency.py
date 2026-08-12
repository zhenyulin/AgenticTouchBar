"""Running provider queries in parallel within the current fetch budget."""

from __future__ import annotations

import time
from typing import Any

from .. import config

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
    """Apply a function to every item while preserving input order."""
    from concurrent.futures import ThreadPoolExecutor

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

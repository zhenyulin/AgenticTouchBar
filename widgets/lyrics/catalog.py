"""Resolving canonical Chinese song titles from Apple's China catalog."""

from __future__ import annotations

from typing import Any

from . import config
from .output import log_error

ITUNES_SEARCH_API = "https://itunes.apple.com/search"


def _contains_han(value: str) -> bool:
    return any("\u3400" <= character <= "\u9fff" for character in value)


def _is_mandapop(track: dict[str, Any]) -> bool:
    genre = str(track.get("genre") or "").casefold()
    return "mandopop" in genre or "manda" in genre


def _catalog_results(track: dict[str, Any]) -> list[dict[str, Any]]:
    import json
    import subprocess
    import urllib.parse

    params = urllib.parse.urlencode(
        {
            "country": "cn",
            "entity": "song",
            "limit": 10,
            "media": "music",
            "term": f"{track.get('artist', '')} {track.get('title', '')}".strip(),
        }
    )
    result = subprocess.run(
        [
            "/usr/bin/curl",
            "--fail",
            "--silent",
            "--show-error",
            "--max-time",
            f"{config.CATALOG_LOOKUP_TIMEOUT_SECONDS:g}",
            "--user-agent",
            config.USER_AGENT,
            f"{ITUNES_SEARCH_API}?{params}",
        ],
        capture_output=True,
        text=True,
        timeout=config.CATALOG_LOOKUP_TIMEOUT_SECONDS + 1.0,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "iTunes catalog request failed")
    payload = json.loads(result.stdout)
    results = payload.get("results") if isinstance(payload, dict) else None
    return (
        [item for item in results if isinstance(item, dict)]
        if isinstance(results, list)
        else []
    )


def catalog_chinese_title(track: dict[str, Any]) -> str:
    """Return a duration-matched Chinese catalog title for a Mandopop track."""
    if track.get("search_title") or not _is_mandapop(track) or not track.get("title"):
        return ""

    try:
        results = _catalog_results(track)
    except Exception as exc:
        log_error(f"China catalog title lookup failed: {exc}")
        return ""

    duration = float(track.get("duration", 0) or 0)
    candidates: list[tuple[float, int, str]] = []
    for index, item in enumerate(results):
        title = str(item.get("trackName") or "").strip()
        candidate_duration = float(item.get("trackTimeMillis", 0) or 0) / 1000
        duration_error = abs(duration - candidate_duration) if duration else 0.0
        if _contains_han(title) and (not duration or duration_error <= 12.0):
            candidates.append((duration_error, index, title))

    return min(candidates, default=(0.0, 0, ""))[2]

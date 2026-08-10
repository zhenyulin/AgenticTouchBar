"""Resolving canonical Chinese song titles from Apple's China catalog."""

from __future__ import annotations

from typing import Any

from . import config
from .output import log_error

ITUNES_SEARCH_API = "https://itunes.apple.com/search"


def _contains_han(value: str) -> bool:
    return any("\u3400" <= character <= "\u9fff" for character in value)


def _catalog_results(track: dict[str, Any]) -> list[dict[str, Any]]:
    import json
    import urllib.error
    import urllib.parse
    import urllib.request

    params = urllib.parse.urlencode(
        {
            "country": "cn",
            "entity": "song",
            "limit": 10,
            "media": "music",
            "term": f"{track.get('artist', '')} {track.get('title', '')}".strip(),
        }
    )
    request = urllib.request.Request(
        f"{ITUNES_SEARCH_API}?{params}",
        headers={"User-Agent": config.USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(
            request, timeout=config.CATALOG_LOOKUP_TIMEOUT_SECONDS
        ) as response:
            payload = json.loads(response.read().decode("utf-8-sig"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"iTunes catalog returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach the iTunes catalog: {exc.reason}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("iTunes catalog returned an invalid response") from exc

    results = payload.get("results") if isinstance(payload, dict) else None
    return (
        [item for item in results if isinstance(item, dict)]
        if isinstance(results, list)
        else []
    )


def catalog_chinese_title(track: dict[str, Any]) -> str:
    """Return a duration-matched Chinese catalog title for a Mandopop track."""
    title = str(track.get("title") or "").strip()
    artist = str(track.get("artist") or "").strip()
    # No sampler supplies a genre, so a Chinese artist is the reachable
    # Mandopop signal. An already-Chinese title is the catalog title: looking
    # it up again costs a round trip and returns the same characters.
    if track.get("search_title") or not title or _contains_han(title):
        return ""
    if not _contains_han(artist):
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

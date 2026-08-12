"""Resolving canonical Chinese song titles from Apple's China catalog."""

from __future__ import annotations

from typing import Any

from .. import config
from ..runtime.output import log_error

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


def catalog_chinese_identity(track: dict[str, Any]) -> tuple[str, str] | None:
    """Return the duration-matched Chinese catalog (title, artist) pair.

    The CN catalog's localized names are the programmatic source for
    translating romanized metadata back into Chinese -- no alias table is
    needed. Either field can drive the lookup: an English title (then the
    Chinese title and artist are both returned) or a romanized artist on an
    already-Chinese title (then the Chinese artist is returned). None when
    the track is not worth a lookup or nothing matched.
    """
    title = str(track.get("title") or "").strip()
    artist = str(track.get("artist") or "").strip()
    genre = str(track.get("genre") or "").strip()
    if track.get("search_title"):
        return None
    title_han = _contains_han(title)
    artist_han = _contains_han(artist)
    needs_title = bool(title) and not title_han
    needs_artist = bool(artist) and not artist_han
    if not needs_title and not needs_artist:
        return None
    if genre:
        folded = genre.casefold()
        if "manda" not in folded and "canto" not in folded:
            return None
    elif not ((needs_title and artist_han) or (needs_artist and title_han)):
        # Without a genre, a Han character in either field is the Chinese
        # signal; without it this is probably not a Chinese track, and a
        # catalog round trip is wasted.
        return None

    try:
        results = _catalog_results(track)
    except Exception as exc:
        log_error(f"China catalog title lookup failed: {exc}")
        return None

    duration = float(track.get("duration", 0) or 0)
    candidates: list[tuple[float, int, str, str]] = []
    for index, item in enumerate(results):
        item_title = str(item.get("trackName") or "").strip()
        item_artist = str(item.get("artistName") or "").strip()
        candidate_duration = float(item.get("trackTimeMillis", 0) or 0) / 1000
        duration_error = abs(duration - candidate_duration) if duration else 0.0
        if _contains_han(item_title) and (not duration or duration_error <= 12.0):
            candidates.append((duration_error, index, item_title, item_artist))

    if not candidates:
        return None
    best = min(candidates, key=lambda item: (item[0], item[1]))
    return best[2], best[3]

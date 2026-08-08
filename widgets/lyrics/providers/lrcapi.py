"""Querying the public, token-free LrcAPI aggregator for Chinese-catalog lyrics."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .. import config
from ..concurrency import map_concurrently
from ..matching import choose_candidate
from ..metadata import local_title_variants, metadata_variants


def lrcapi_request(params: dict[str, Any]) -> Any:
    """Query the public token-free LrcAPI advance endpoint."""
    import urllib.error
    import urllib.parse
    import urllib.request

    clean_params = {
        key: value for key, value in params.items() if value not in {None, ""}
    }
    url = f"{config.LRCAPI_API_BASE}/advance?{urllib.parse.urlencode(clean_params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": config.USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(
            request, timeout=config.LRCAPI_TIMEOUT_SECONDS
        ) as response:
            payload = response.read()
        if not payload:
            return []
        return json.loads(payload.decode("utf-8-sig"))
    except urllib.error.HTTPError as exc:
        if exc.code in {404, 422}:
            return []
        raise RuntimeError(f"LrcAPI returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach LrcAPI: {exc.reason}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("LrcAPI returned an invalid response") from exc


def lrcapi_single_request(params: dict[str, Any]) -> str:
    """Query LrcAPI's single-result endpoint, which returns raw LRC text."""
    import urllib.error
    import urllib.parse
    import urllib.request

    clean_params = {
        key: value for key, value in params.items() if value not in {None, ""}
    }
    url = f"{config.LRCAPI_API_BASE}/single?{urllib.parse.urlencode(clean_params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": config.USER_AGENT, "Accept": "text/plain, text/html"},
    )
    try:
        with urllib.request.urlopen(
            request, timeout=config.LRCAPI_TIMEOUT_SECONDS
        ) as response:
            payload = response.read()
        return payload.decode("utf-8-sig", errors="replace").strip()
    except urllib.error.HTTPError as exc:
        if exc.code in {404, 422}:
            return ""
        raise RuntimeError(f"LrcAPI single endpoint returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Could not reach LrcAPI single endpoint: {exc.reason}"
        ) from exc


def adapt_lrcapi_candidate(item: dict[str, Any]) -> dict[str, Any] | None:
    lyrics = str(item.get("lyrics") or "")
    if not config.TIMESTAMP_RE.search(lyrics):
        return None
    provider_id = str(
        item.get("id") or hashlib.sha256(lyrics.encode("utf-8")).hexdigest()[:20]
    )
    return {
        "id": f"lrcapi:{provider_id}",
        "providerId": provider_id,
        "source": "lrcapi",
        "trackName": str(item.get("title") or ""),
        "artistName": str(item.get("artist") or ""),
        "albumName": str(item.get("album") or ""),
        "duration": 0,
        "instrumental": False,
        "plainLyrics": None,
        "syncedLyrics": lyrics,
    }


def collect_lrcapi_candidates(track: dict[str, Any]) -> list[dict[str, Any]]:
    """Search Chinese providers through LrcAPI without an API token."""
    if not config.ENABLE_LRCAPI:
        return []

    titles = local_title_variants(track.get("title", ""))[:4]
    artists = metadata_variants(track.get("artist", ""))[:6]
    album_variants = local_title_variants(track.get("album", ""))[:3]
    queries: list[dict[str, str]] = []
    query_signatures: set[tuple[tuple[str, str], ...]] = set()

    def add_query(title: str, artist: str = "", album_name: str = "") -> None:
        query = {"title": title, "artist": artist, "album": album_name}
        signature = tuple(query.items())
        if title and signature not in query_signatures:
            query_signatures.add(signature)
            queries.append(query)

    if not titles:
        return []

    original_title = titles[0]
    stripped_title = titles[1] if len(titles) > 1 else original_title
    original_artist = artists[0] if artists else ""
    chinese_artists = [
        artist
        for artist in artists
        if any("\u3400" <= char <= "\u9fff" for char in artist)
    ]
    preferred_chinese_artist = (
        chinese_artists[0] if chinese_artists else original_artist
    )
    original_album = album_variants[0] if album_variants else ""
    stripped_album = album_variants[1] if len(album_variants) > 1 else original_album

    # High-value combinations first. In particular, pair a subtitle-free
    # Chinese title with the Chinese artist alias; v4 did not test this pair.
    add_query(original_title, original_artist, original_album)
    add_query(stripped_title, preferred_chinese_artist, stripped_album)
    add_query(stripped_title, preferred_chinese_artist)
    add_query(original_title, preferred_chinese_artist)
    add_query(stripped_title, original_artist)
    add_query(original_title, original_artist)

    # Add a small title × artist matrix, while keeping network work bounded.
    for title in titles[:3]:
        for artist in artists[:4]:
            add_query(title, artist)
    add_query(stripped_title)

    attempted = queries[: max(config.LRCAPI_MAX_ADVANCE_QUERIES, 0)]
    collected: list[dict[str, Any]] = []
    seen: set[str] = set()
    errors: list[str] = []

    for query, (payload, error) in zip(
        attempted, map_concurrently(lrcapi_request, attempted)
    ):
        if error is not None:
            errors.append(str(error))
            continue
        if not isinstance(payload, list):
            continue
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            candidate = adapt_lrcapi_candidate(raw)
            if candidate is None:
                continue
            candidate["sourceQuery"] = query
            identity = str(candidate.get("id"))
            if identity not in seen:
                seen.add(identity)
                collected.append(candidate)

    # The documented /single endpoint sometimes succeeds when /advance
    # returns an empty candidate array. Restrict it to strong title+artist
    # queries to reduce false matches.
    if not collected:
        single_queries = [query for query in queries if query.get("artist")][
            : max(config.LRCAPI_MAX_SINGLE_QUERIES, 0)
        ]
        for query, (lyrics, error) in zip(
            single_queries, map_concurrently(lrcapi_single_request, single_queries)
        ):
            if error is not None:
                errors.append(str(error))
                continue
            if not lyrics or not config.TIMESTAMP_RE.search(lyrics):
                continue
            provider_id = hashlib.sha256(
                (json.dumps(query, ensure_ascii=False, sort_keys=True) + lyrics).encode(
                    "utf-8"
                )
            ).hexdigest()[:20]
            collected.append(
                {
                    "id": f"lrcapi-single:{provider_id}",
                    "providerId": provider_id,
                    "source": "lrcapi-single",
                    "trackName": query["title"],
                    "artistName": query["artist"],
                    "albumName": query.get("album", ""),
                    "duration": track.get("duration", 0),
                    "instrumental": False,
                    "plainLyrics": None,
                    "syncedLyrics": lyrics,
                    "sourceQuery": query,
                }
            )
            break

    if not collected and attempted and len(errors) >= len(attempted):
        raise RuntimeError(errors[0])
    return collected


def choose_lrcapi_candidate(track: dict[str, Any]) -> dict[str, Any] | None:
    return choose_candidate(track, collect_lrcapi_candidates(track))

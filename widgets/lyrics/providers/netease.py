"""Querying NetEase Cloud Music's public search and lyric endpoints.

NetEase carries genuinely timed LRC for most Chinese catalog tracks where
LRCLIB only has plain text and LrcAPI is silent; its search and lyric
endpoints are public and token-free (song lyric downloads require no login).
"""

from __future__ import annotations

import json
from typing import Any

from .. import config
from ..runtime.concurrency import fetch_seconds_remaining, map_concurrently
from ..text.lrc import strip_credit_lines
from ..text.matching import candidate_score
from ..text.metadata import track_artist_variants, track_title_variants


def netease_request(path: str, params: dict[str, str]) -> Any:
    """GET a NetEase API endpoint, returning parsed JSON or None on a miss."""
    import urllib.error
    import urllib.parse
    import urllib.request

    clean_params = {
        key: value for key, value in params.items() if value not in {None, ""}
    }
    url = f"{config.NETEASE_API_BASE}{path}?{urllib.parse.urlencode(clean_params)}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": config.USER_AGENT,
            "Accept": "application/json",
            "Referer": "https://music.163.com/",
        },
    )
    try:
        with urllib.request.urlopen(
            request, timeout=config.NETEASE_TIMEOUT_SECONDS
        ) as response:
            payload = response.read()
        if not payload:
            return None
        return json.loads(payload.decode("utf-8-sig"))
    except urllib.error.HTTPError as exc:
        if exc.code in {404, 422}:
            return None
        raise RuntimeError(f"NetEase returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach NetEase: {exc.reason}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("NetEase returned an invalid response") from exc


def netease_search_results(track: dict[str, Any]) -> list[dict[str, Any]]:
    titles = track_title_variants(track)[:2]
    artists = track_artist_variants(track)[:2]
    if not titles:
        return []

    queries: list[dict[str, str]] = []
    if artists:
        queries.append({"s": f"{titles[0]} {artists[0]}", "type": "1", "limit": "8"})
    queries.append({"s": titles[0], "type": "1", "limit": "8"})
    attempted = queries[: max(config.NETEASE_MAX_SEARCH_QUERIES, 0)]

    if fetch_seconds_remaining() < config.NETWORK_TIMEOUT_SECONDS:
        return []

    collected: list[dict[str, Any]] = []
    seen: set[int] = set()
    errors: list[str] = []
    for query, (payload, error) in zip(
        attempted,
        map_concurrently(
            lambda query: netease_request("/api/search/get/web", query), attempted
        ),
    ):
        if error is not None:
            errors.append(str(error))
            continue
        if not isinstance(payload, dict) or payload.get("code") != 200:
            continue
        for song in (payload.get("result") or {}).get("songs") or []:
            if not isinstance(song, dict):
                continue
            song_id = int(song.get("id") or 0)
            if song_id and song_id not in seen:
                seen.add(song_id)
                collected.append(song)

    if not collected and attempted and len(errors) >= len(attempted):
        raise RuntimeError(errors[0])
    return collected


def netease_lyrics(song_id: int) -> str:
    """Fetch one song's synced LRC, with NetEase credit lines filtered out."""
    payload = netease_request(
        "/api/song/lyric",
        {"id": str(song_id), "lv": "1", "kv": "1", "tv": "-1"},
    )
    if not isinstance(payload, dict) or payload.get("code") != 200:
        return ""
    lyrics = str((payload.get("lrc") or {}).get("lyric") or "")
    return strip_credit_lines(lyrics)


def as_netease_record(song: dict[str, Any], lyrics: str) -> dict[str, Any]:
    artists = [a.get("name", "") for a in song.get("artists") or [] if a]
    return {
        "id": f"netease:{song.get('id')}",
        "providerId": str(song.get("id")),
        "source": "netease",
        "trackName": str(song.get("name") or ""),
        "artistName": " / ".join(artists),
        "albumName": str((song.get("album") or {}).get("name") or ""),
        "duration": float(song.get("duration") or 0) / 1000.0,
        "instrumental": False,
        "plainLyrics": None,
        "syncedLyrics": lyrics,
    }


def netease_record(track: dict[str, Any]) -> dict[str, Any] | None:
    """Search NetEase and return the best candidate with a timed LRC, or None."""
    if not config.ENABLE_NETEASE:
        return None

    songs = netease_search_results(track)
    candidates = [
        (song, candidate_score(track, as_netease_record(song, ""))) for song in songs
    ]
    candidates = [(song, score) for song, score in candidates if score >= 0.60]
    candidates.sort(key=lambda item: item[1], reverse=True)
    candidates = candidates[: max(config.NETEASE_MAX_LYRIC_FETCHES, 0)]
    if not candidates:
        return None
    if fetch_seconds_remaining() < config.NETWORK_TIMEOUT_SECONDS:
        return None

    fetches = map_concurrently(
        lambda song: netease_lyrics(int(song.get("id") or 0)),
        [song for song, _ in candidates],
    )
    for (song, _), (lyrics, error) in zip(candidates, fetches):
        if error is None and lyrics and config.TIMESTAMP_RE.search(lyrics):
            return as_netease_record(song, lyrics)
    return None

"""Querying Tencent QQ Music's public search and lyric endpoints.

QQ Music carries genuinely timed LRC for most Chinese catalog tracks; its
search and lyric endpoints are public and token-free, but the lyric endpoint
rejects requests without a y.qq.com referer.
"""

from __future__ import annotations

import json
from typing import Any

from .. import config
from ..runtime.concurrency import fetch_seconds_remaining, map_concurrently
from ..text.lrc import strip_credit_lines
from ..text.matching import candidate_score
from ..text.metadata import track_artist_variants, track_title_variants

QQMUSIC_REFERER = "https://y.qq.com/"


def qqmusic_request(path: str, params: dict[str, str]) -> Any:
    """GET a QQ Music endpoint, returning parsed JSON or None on a miss."""
    import urllib.error
    import urllib.parse
    import urllib.request

    clean_params = {
        key: value for key, value in params.items() if value not in {None, ""}
    }
    url = f"{config.QQMUSIC_API_BASE}{path}?{urllib.parse.urlencode(clean_params)}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": config.USER_AGENT,
            "Accept": "application/json",
            "Referer": QQMUSIC_REFERER,
        },
    )
    try:
        with urllib.request.urlopen(
            request, timeout=config.QQMUSIC_TIMEOUT_SECONDS
        ) as response:
            payload = response.read()
        if not payload:
            return None
        return json.loads(payload.decode("utf-8-sig"))
    except urllib.error.HTTPError as exc:
        if exc.code in {403, 404, 422}:
            return None
        raise RuntimeError(f"QQ Music returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach QQ Music: {exc.reason}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("QQ Music returned an invalid response") from exc


def qqmusic_search_results(track: dict[str, Any]) -> list[dict[str, Any]]:
    titles = track_title_variants(track)[:2]
    artists = track_artist_variants(track)[:2]
    if not titles:
        return []

    queries: list[dict[str, str]] = []
    if artists:
        queries.append(
            {"w": f"{titles[0]} {artists[0]}", "format": "json", "p": "1", "n": "10"}
        )
    queries.append({"w": titles[0], "format": "json", "p": "1", "n": "10"})
    attempted = queries[: max(config.QQMUSIC_MAX_SEARCH_QUERIES, 0)]

    if fetch_seconds_remaining() < config.NETWORK_TIMEOUT_SECONDS:
        return []

    collected: list[dict[str, Any]] = []
    seen: set[str] = set()
    errors: list[str] = []
    for query, (payload, error) in zip(
        attempted,
        map_concurrently(
            lambda query: qqmusic_request("/soso/fcgi-bin/client_search_cp", query),
            attempted,
        ),
    ):
        if error is not None:
            errors.append(str(error))
            continue
        if not isinstance(payload, dict) or payload.get("code") != 0:
            continue
        for song in ((payload.get("data") or {}).get("song") or {}).get("list") or []:
            if not isinstance(song, dict) or not song.get("songmid"):
                continue
            mid = str(song["songmid"])
            if mid not in seen:
                seen.add(mid)
                collected.append(song)

    if not collected and attempted and len(errors) >= len(attempted):
        raise RuntimeError(errors[0])
    return collected


def qqmusic_lyrics(song: dict[str, Any]) -> str:
    """Fetch one song's synced LRC, with credit and intro lines filtered."""
    payload = qqmusic_request(
        "/lyric/fcgi-bin/fcg_query_lyric_new.fcg",
        {
            "songmid": str(song.get("songmid") or ""),
            "format": "json",
            "nobase64": "1",
        },
    )
    if not isinstance(payload, dict) or payload.get("retcode") != 0:
        return ""
    lyrics = str(payload.get("lyric") or "")
    artists = [a.get("name", "") for a in song.get("singer") or [] if a]
    artist_str = " / ".join(artists)
    title = str(song.get("songname") or "")
    # QQ LRCs open with a "title - artist" intro line at 00:00.
    intro_variants = {f"{title} - {artist_str}", f"{artist_str} - {title}"}
    return "\n".join(
        line
        for line in strip_credit_lines(lyrics).splitlines()
        if config.TIMESTAMP_RE.sub("", line).strip() not in intro_variants
    )


def as_qqmusic_record(song: dict[str, Any], lyrics: str) -> dict[str, Any]:
    artists = [a.get("name", "") for a in song.get("singer") or [] if a]
    return {
        "id": f"qqmusic:{song.get('songmid')}",
        "providerId": str(song.get("songmid")),
        "source": "qqmusic",
        "trackName": str(song.get("songname") or ""),
        "artistName": " / ".join(artists),
        "albumName": str(song.get("albumname") or ""),
        "duration": float(song.get("interval") or 0),
        "instrumental": False,
        "plainLyrics": None,
        "syncedLyrics": lyrics,
    }


def qqmusic_record(track: dict[str, Any]) -> dict[str, Any] | None:
    """Search QQ Music and return the best candidate with a timed LRC, or None."""
    if not config.ENABLE_QQMUSIC:
        return None

    songs = qqmusic_search_results(track)
    candidates = [
        (song, candidate_score(track, as_qqmusic_record(song, ""))) for song in songs
    ]
    candidates = [(song, score) for song, score in candidates if score >= 0.60]
    candidates.sort(key=lambda item: item[1], reverse=True)
    candidates = candidates[: max(config.QQMUSIC_MAX_LYRIC_FETCHES, 0)]
    if not candidates:
        return None
    if fetch_seconds_remaining() < config.NETWORK_TIMEOUT_SECONDS:
        return None

    fetches = map_concurrently(
        lambda song: qqmusic_lyrics(song), [song for song, _ in candidates]
    )
    for (song, _), (lyrics, error) in zip(candidates, fetches):
        if error is None and lyrics and config.TIMESTAMP_RE.search(lyrics):
            return as_qqmusic_record(song, lyrics)
    return None

"""Querying the open LRCLIB lyrics database."""

from __future__ import annotations

import json
import time
from typing import Any

from .. import config
from ..concurrency import fetch_seconds_remaining, map_concurrently
from ..matching import choose_candidate
from ..metadata import metadata_variants, normalized, track_title_variants
from ..output import log_error


def lrclib_api_request(
    endpoint: str,
    params: dict[str, Any],
    attempts: int = config.LRCLIB_RETRY_ATTEMPTS,
) -> Any:
    import urllib.error
    import urllib.parse
    import urllib.request

    clean_params = {
        key: value for key, value in params.items() if value not in {None, ""}
    }
    url = f"{config.LRCLIB_API_BASE}/{endpoint}?{urllib.parse.urlencode(clean_params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": config.USER_AGENT, "Accept": "application/json"},
    )

    last_error: Exception | None = None
    for attempt in range(max(attempts, 1)):
        if attempt:
            backoff = config.LRCLIB_RETRY_BACKOFF_SECONDS * attempt
            if fetch_seconds_remaining() < backoff + config.NETWORK_TIMEOUT_SECONDS:
                break
            time.sleep(backoff)
        try:
            with urllib.request.urlopen(
                request, timeout=config.NETWORK_TIMEOUT_SECONDS
            ) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            last_error = RuntimeError(f"LRCLIB returned HTTP {exc.code}")
            if exc.code not in config.TRANSIENT_HTTP_STATUS:
                break
        except urllib.error.URLError as exc:
            last_error = RuntimeError(f"Could not reach LRCLIB: {exc.reason}")
        except (TimeoutError, OSError) as exc:
            last_error = RuntimeError(f"Could not reach LRCLIB: {exc}")

    raise last_error or RuntimeError("LRCLIB request failed")


def collect_search_candidates(track: dict[str, Any]) -> list[dict[str, Any]]:
    title_variants = track_title_variants(track)[:4]
    artist_variants = metadata_variants(track.get("artist", ""))[:6]
    collected: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_results(results: Any) -> None:
        if not isinstance(results, list):
            return
        for item in results:
            if not isinstance(item, dict):
                continue
            identity = str(
                item.get("id")
                or (
                    normalized(item.get("trackName", "")),
                    normalized(item.get("artistName", "")),
                    round(float(item.get("duration", 0) or 0)),
                )
            )
            if identity not in seen:
                seen.add(identity)
                collected.append(item)

    queries: list[dict[str, str]] = []

    def add_query(query: dict[str, str]) -> None:
        if query.get("track_name") or query.get("q"):
            queries.append(query)

    if title_variants:
        original_title = title_variants[0]
        original_artist = artist_variants[0] if artist_variants else ""
        add_query({"track_name": original_title, "artist_name": original_artist})

    for title in title_variants[:3]:
        add_query({"track_name": title})

    if title_variants:
        title = title_variants[0]
        add_query({"q": title})
        for artist in artist_variants[1:5]:
            add_query({"q": f"{title} {artist}".strip()})

    attempted = queries[: max(config.LRCLIB_MAX_SEARCH_QUERIES, 0)]
    if fetch_seconds_remaining() < config.FETCH_TIMEOUT_SECONDS:
        attempted = []

    errors: list[Exception] = []
    for results, error in map_concurrently(
        lambda query: lrclib_api_request("search", query), attempted
    ):
        if error is not None:
            errors.append(error)
        else:
            add_results(results)

    if not collected and errors:
        raise errors[0]
    return collected


def lrclib_exact_record(track: dict[str, Any]) -> dict[str, Any] | None:
    title_variants = track_title_variants(track)[:3]
    artist_variants = metadata_variants(track.get("artist", ""))[:5]
    duration = round(float(track.get("duration", 0))) or None

    exact = lrclib_api_request(
        "get",
        {
            "track_name": title_variants[0]
            if title_variants
            else track.get("title", ""),
            "artist_name": artist_variants[0]
            if artist_variants
            else track.get("artist", ""),
            "album_name": track.get("album", ""),
            "duration": duration,
        },
        attempts=1,
    )
    if isinstance(exact, dict) and (
        exact.get("syncedLyrics") or exact.get("instrumental")
    ):
        return dict(exact)
    return None


def as_lrclib_record(record: dict[str, Any]) -> dict[str, Any]:
    record = dict(record)
    record.setdefault("source", "lrclib")
    record.setdefault("providerId", record.get("id"))
    return record


def lrclib_record(track: dict[str, Any]) -> dict[str, Any] | None:
    exact_error: Exception | None = None
    try:
        exact = lrclib_exact_record(track)
    except Exception as exc:
        log_error(f"LRCLIB exact lookup failed; trying search: {exc}")
        exact_error, exact = exc, None

    if exact is not None:
        return as_lrclib_record(exact)

    try:
        selected = choose_candidate(track, collect_search_candidates(track))
    except Exception as exc:
        raise exact_error or exc

    if selected is None:
        if exact_error is not None:
            raise exact_error
        return None
    return as_lrclib_record(selected)

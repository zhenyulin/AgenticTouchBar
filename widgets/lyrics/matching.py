"""Scoring and choosing the best lyrics candidate for a track."""

from __future__ import annotations

from typing import Any

from .metadata import best_similarity, track_artist_variants, track_title_variants


def candidate_score(track: dict[str, Any], candidate: dict[str, Any]) -> float:
    title_score = max(
        best_similarity(variant, candidate.get("trackName", ""))
        for variant in track_title_variants(track)
    )
    artist_score = max(
        best_similarity(variant, candidate.get("artistName", ""))
        for variant in track_artist_variants(track)
    )
    album_score = best_similarity(
        track.get("album", ""), candidate.get("albumName", "")
    )

    duration = float(track.get("duration", 0) or 0)
    candidate_duration = float(candidate.get("duration", 0) or 0)
    duration_error = (
        abs(duration - candidate_duration) if duration and candidate_duration else None
    )
    duration_score = (
        max(0.0, 1.0 - duration_error / 30.0) if duration_error is not None else 0.45
    )

    if title_score < 0.45:
        return -1.0
    duration_close = duration_error is not None and duration_error <= 10.0
    artist_alias_safe = artist_score >= 0.75 and duration_close
    title_alias_safe = title_score >= 0.88 and duration_close
    if title_score < 0.55 and not artist_alias_safe:
        return -1.0
    if artist_score < 0.18 and not title_alias_safe:
        return -1.0

    return (
        0.62 * title_score
        + 0.18 * artist_score
        + 0.05 * album_score
        + 0.15 * duration_score
    )


def choose_candidate(
    track: dict[str, Any], candidates: list[dict[str, Any]]
) -> dict[str, Any] | None:
    usable = [
        item
        for item in candidates
        if isinstance(item, dict)
        and (
            item.get("syncedLyrics")
            or item.get("plainLyrics")
            or item.get("instrumental")
        )
    ]
    if not usable:
        return None

    # Timed lyrics beat plain text; an explicitly instrumental entry only
    # wins when neither is available.
    def priority(item: dict[str, Any]) -> int:
        if item.get("syncedLyrics"):
            return 2
        if item.get("plainLyrics"):
            return 1
        return 0

    best = max(usable, key=lambda item: (priority(item), candidate_score(track, item)))
    best_score = candidate_score(track, best)
    return best if best_score >= 0.60 else None

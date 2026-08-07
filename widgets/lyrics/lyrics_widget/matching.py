"""Scoring and choosing the best lyrics candidate for a track."""

from __future__ import annotations

from typing import Any

from .metadata import best_similarity


def candidate_score(track: dict[str, Any], candidate: dict[str, Any]) -> float:
    title_score = best_similarity(
        track.get("title", ""), candidate.get("trackName", "")
    )
    artist_score = best_similarity(
        track.get("artist", ""), candidate.get("artistName", "")
    )
    album_score = best_similarity(
        track.get("album", ""), candidate.get("albumName", "")
    )

    duration = float(track.get("duration", 0) or 0)
    candidate_duration = float(candidate.get("duration", 0) or 0)
    duration_error = (
        abs(duration - candidate_duration) if duration and candidate_duration else None
    )
    if duration_error is not None:
        duration_score = max(0.0, 1.0 - duration_error / 30.0)
    else:
        duration_score = 0.45

    # Title is the strongest identity signal. Permit catalog/community artist
    # aliases when the title is an excellent match and duration is close.
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
        and (item.get("syncedLyrics") or item.get("instrumental"))
    ]
    if not usable:
        return None
    ranked = sorted(
        ((candidate_score(track, item), item) for item in usable),
        key=lambda pair: pair[0],
        reverse=True,
    )
    best_score, best = ranked[0]
    return best if best_score >= 0.60 else None

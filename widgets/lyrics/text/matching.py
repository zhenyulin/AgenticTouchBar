"""Scoring and choosing the best lyrics candidate for a track."""

from __future__ import annotations

from typing import Any

from .metadata import (
    best_similarity,
    strict_identity_match,
    track_artist_variants,
    track_title_variants,
)


def candidate_score(track: dict[str, Any], candidate: dict[str, Any]) -> float:
    title_score = max(
        (
            best_similarity(variant, candidate.get("trackName", ""))
            for variant in track_title_variants(track)
        ),
        default=0.0,
    )
    artist_score = max(
        (
            best_similarity(variant, candidate.get("artistName", ""))
            for variant in track_artist_variants(track)
        ),
        default=0.0,
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

    # Strict identity gate: the candidate must be the same song by the same
    # act once annotations are trimmed on both sides. A strong title no
    # longer vouches for a mismatched artist, and a strong artist no longer
    # vouches for a mismatched title; alias groups and converted scripts
    # still count as exact. The artist keeps containment ("The Beatles" vs
    # "Beatles"), the title never does ("Yellow" must not accept "Yellow
    # Submarine").
    if not any(
        strict_identity_match(variant, candidate.get("trackName", ""))
        for variant in track_title_variants(track)
    ):
        return -1.0
    if not any(
        strict_identity_match(
            variant, candidate.get("artistName", ""), allow_containment=True
        )
        for variant in track_artist_variants(track)
    ):
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

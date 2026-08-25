"""Scoring and choosing the best lyrics candidate for a track."""

from __future__ import annotations

from typing import Any

from .metadata import (
    best_similarity,
    contains_han,
    strict_identity_match,
    track_artist_variants,
    track_title_variants,
)


def _is_chinese_song(
    track: dict[str, Any], candidate_title: str, candidate_artist: str
) -> bool:
    """Whether this candidate race concerns a Chinese song.

    A Han character anywhere in the track metadata is the signal. A
    romanised Chinese title (Apple reports "1989" by "Soundtoy" instead of
    声音玩具) carries no Han itself, so the Manda/Canto genre plus the
    candidate's Han spelling stand in for it. The candidate alone can never
    mark a track Chinese -- "Yellow" by "Coldplay" stays strict even
    against a Han-spelled hit.
    """
    if any(
        contains_han(str(track.get(key) or ""))
        for key in ("search_title", "title", "search_artist", "artist")
    ):
        return True
    return any(
        token in str(track.get("genre") or "").casefold()
        for token in ("manda", "mando", "mandar", "canto")
    ) and (contains_han(candidate_title) or contains_han(candidate_artist))


def candidate_score(track: dict[str, Any], candidate: dict[str, Any]) -> float:
    title_variants = track_title_variants(track)
    artist_variants = track_artist_variants(track)
    candidate_title = str(candidate.get("trackName") or "")
    candidate_artist = str(candidate.get("artistName") or "")

    title_score = max(
        (best_similarity(variant, candidate_title) for variant in title_variants),
        default=0.0,
    )
    artist_score = max(
        (best_similarity(variant, candidate_artist) for variant in artist_variants),
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

    duration_close = duration_error is not None and duration_error <= 10.0

    if _is_chinese_song(track, candidate_title, candidate_artist):
        # Chinese songs keep the pre-tightening scoring floors: romanised
        # artist strings are unreliable (Apple's "Soundtoy" is the
        # catalogues' 声音玩具) and the catalogues carry many renderings of
        # one title, so the similarity gates apply again -- a strong title
        # can vouch for the artist. Annotation trimming and alias handling
        # still sharpen the scores themselves.
        if title_score < 0.45:
            return -1.0
        artist_alias_safe = artist_score >= 0.75 and duration_close
        title_alias_safe = title_score >= 0.88 and duration_close
        if title_score < 0.55 and not artist_alias_safe:
            return -1.0
        if artist_score < 0.18 and not title_alias_safe:
            return -1.0
    else:
        # Strict identity gate: the candidate must be the same song by the
        # same act once annotations are trimmed on both sides. Alias groups
        # and converted scripts count as exact. The artist keeps containment
        # ("The Beatles" vs "Beatles"), the title never does ("Yellow" must
        # not accept "Yellow Submarine").
        if not any(
            strict_identity_match(variant, candidate_title)
            for variant in title_variants
        ):
            return -1.0
        if not any(
            strict_identity_match(variant, candidate_artist, allow_containment=True)
            for variant in artist_variants
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

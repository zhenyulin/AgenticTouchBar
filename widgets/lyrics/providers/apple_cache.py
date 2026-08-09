"""Reading the time-synced TTML that Apple Music leaves in its own URL cache.

Music fetches ``…/wa/ttmlLyrics?id=<adamID>`` for the catalog track it is about
to display and stores the response in a plain NSURLCache. The payload is
unencrypted JSON wrapping a TTML document with per-line timings, so it can be
converted to LRC and handed to the ordinary pipeline.

Refetching that URL ourselves is not an option: it is rejected without Apple's
per-request ``X-Apple-ActionSignature`` device attestation, which cannot be
generated outside Music. The cache is therefore the only local access path, and
it is an LRU holding roughly a day of playback — a fast path, not a library.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
import unicodedata
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from .. import config
from ..output import log_error

TTML_NS = "{http://www.w3.org/ns/ttml}"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
# "12.345", "1:03.226" or "01:02:03.226" all appear across catalogs.
CLOCK_RE = re.compile(r"^(?:(?:(\d+):)?(\d+):)?(\d+(?:\.\d+)?)$")


def _clock_seconds(value: str) -> float | None:
    match = CLOCK_RE.match(value.strip())
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours or 0) * 3600 + int(minutes or 0) * 60 + float(seconds)


def _line_text(paragraph: ElementTree.Element) -> str:
    """Flatten a <p>, including the <span> per word that word-timed TTML uses."""
    return " ".join("".join(paragraph.itertext()).split())


def _ttml_to_lrc(ttml: str) -> tuple[str, float | None, str]:
    """Convert TTML to LRC text, duration, and declared language."""
    root = ElementTree.fromstring(ttml)

    body = root.find(f"{TTML_NS}body")
    duration = _clock_seconds(body.get("dur", "")) if body is not None else None
    language = root.get(XML_LANG, "")

    lines: list[tuple[float, str]] = []
    for paragraph in root.iter(f"{TTML_NS}p"):
        begin = _clock_seconds(paragraph.get("begin", ""))
        text = _line_text(paragraph)
        if begin is not None and text:
            lines.append((begin, text))

    lines.sort(key=lambda item: item[0])
    rendered = "\n".join(
        f"[{int(begin) // 60:02d}:{begin % 60:06.3f}]{text}" for begin, text in lines
    )
    return rendered, duration, language


def _track_uses_cjk(track: dict[str, Any]) -> bool:
    return any(
        unicodedata.east_asian_width(character) in {"W", "F"}
        for field in ("title", "artist", "album")
        for character in str(track.get(field, "") or "")
    )


def _language_matches_track(track: dict[str, Any], language: str) -> bool:
    if not language or not _track_uses_cjk(track):
        return True
    return language.casefold().startswith(("zh", "ja", "ko"))


def _cached_rows() -> list[tuple[str, str, int]]:
    """The newest cached ttmlLyrics entries, as (request_key, blob, on_fs)."""
    if not config.APPLE_MUSIC_CACHE_DB.is_file():
        return []

    # Music holds the database open in WAL mode, so a read-only handle cannot
    # see committed-but-uncheckpointed rows — which is exactly where the track
    # playing right now lives. Copy the set aside and query that.
    with tempfile.TemporaryDirectory() as workspace:
        copy = Path(workspace) / "Cache.db"
        for suffix in ("", "-wal", "-shm"):
            source = config.APPLE_MUSIC_CACHE_DB.with_name(
                config.APPLE_MUSIC_CACHE_DB.name + suffix
            )
            try:
                shutil.copyfile(source, Path(str(copy) + suffix))
            except FileNotFoundError:
                continue  # A checkpointed cache has no sidecars.
            except OSError as exc:
                log_error(f"Could not copy Apple Music cache {source}: {exc}")
                return []

        try:
            connection = sqlite3.connect(copy)
            try:
                return connection.execute(
                    """
                    SELECT response.request_key,
                           data.receiver_data,
                           data.isDataOnFS
                    FROM cfurl_cache_response AS response
                    JOIN cfurl_cache_receiver_data AS data
                      ON data.entry_ID = response.entry_ID
                    WHERE response.request_key LIKE '%ttmlLyrics%'
                    ORDER BY response.time_stamp DESC
                    LIMIT ?
                    """,
                    (config.APPLE_MUSIC_CACHE_MAX_ROWS,),
                ).fetchall()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            log_error(f"Could not read Apple Music lyrics cache: {exc}")
            return []


def _payload(blob: Any, on_fs: int) -> dict[str, Any] | None:
    if on_fs:
        name = blob.decode("utf-8") if isinstance(blob, bytes) else str(blob)
        try:
            blob = (config.APPLE_MUSIC_CACHE_FS_DIR / name).read_bytes()
        except OSError:
            return None  # Evicted between the query and the read.

    try:
        payload = json.loads(blob)
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def apple_cache_record(track: dict[str, Any]) -> dict[str, Any] | None:
    """Return synced lyrics for the track if Apple Music has them cached."""
    track_duration = float(track.get("duration", 0.0) or 0.0)
    if not track_duration:
        # Without a duration there is nothing to match on, and picking the newest
        # entry blindly would caption the track with the previous song's lyrics.
        return None

    best: tuple[float, str, str, str] | None = None
    for request_key, blob, on_fs in _cached_rows():
        payload = _payload(blob, on_fs)
        ttml = (payload or {}).get("ttml")
        if not ttml:
            continue

        try:
            synced, ttml_duration, language = _ttml_to_lrc(ttml)
        except ElementTree.ParseError as exc:
            log_error(f"Could not parse cached Apple Music TTML: {exc}")
            continue

        if not synced or ttml_duration is None:
            continue
        if not _language_matches_track(track, language):
            continue
        gap = abs(ttml_duration - track_duration)
        if gap > config.APPLE_MUSIC_CACHE_DURATION_TOLERANCE:
            continue

        # Take the closest duration rather than the most recently cached: if two
        # entries somehow both qualify, recency says nothing about which song is
        # playing, while the gap does.
        lyrics_id = (payload or {}).get("lyricsId") or "apple-cache"
        if best is None or gap < best[0]:
            best = (gap, synced, lyrics_id, request_key)

    if best is None:
        return None

    _, synced, lyrics_id, request_key = best
    return {
        "id": lyrics_id,
        "trackName": track.get("title", ""),
        "artistName": track.get("artist", ""),
        "albumName": track.get("album", ""),
        "duration": track_duration,
        "instrumental": False,
        "plainLyrics": None,
        "syncedLyrics": synced,
        "source": "apple-cache",
        "requestKey": request_key,
    }

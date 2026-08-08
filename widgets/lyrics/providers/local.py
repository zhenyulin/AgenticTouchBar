"""Loading a synchronized .lrc from the user-maintained local lyrics directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import config
from ..metadata import metadata_variants, normalized, track_title_variants
from ..output import log_error


def local_lyrics_record(track: dict[str, Any]) -> dict[str, Any] | None:
    """Load a synchronized .lrc from the local lyrics directory when available."""
    if not config.LOCAL_LYRICS_DIR.is_dir():
        return None

    titles = track_title_variants(track, local=True)
    artists = metadata_variants(track.get("artist", ""))
    candidate_paths: list[Path] = []
    seen: set[Path] = set()

    # Prefer deterministic names: "Artist - Title.lrc" or "Title.lrc".
    for title in titles:
        for filename in (f"{title}.lrc",):
            path = config.LOCAL_LYRICS_DIR / filename
            if path not in seen:
                seen.add(path)
                candidate_paths.append(path)
        for artist in artists:
            for separator in (" - ", " — "):
                path = config.LOCAL_LYRICS_DIR / f"{artist}{separator}{title}.lrc"
                if path not in seen:
                    seen.add(path)
                    candidate_paths.append(path)

    # Then scan the small local directory for equivalent simplified/traditional
    # metadata or slightly different punctuation.
    title_forms = {normalized(item) for item in titles if normalized(item)}
    artist_forms = {normalized(item) for item in artists if normalized(item)}
    try:
        for path in config.LOCAL_LYRICS_DIR.glob("*.lrc"):
            stem = normalized(path.stem)
            title_match = any(form and form in stem for form in title_forms)
            artist_match = not artist_forms or any(
                form and form in stem for form in artist_forms
            )
            if title_match and artist_match and path not in seen:
                seen.add(path)
                candidate_paths.append(path)
    except OSError as exc:
        log_error(
            f"Could not scan local lyrics directory {config.LOCAL_LYRICS_DIR}: {exc}"
        )

    for path in candidate_paths:
        try:
            synced = path.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            continue
        except OSError as exc:
            log_error(f"Could not read local LRC {path}: {exc}")
            continue

        if not config.TIMESTAMP_RE.search(synced):
            log_error(f"Local lyrics file has no LRC timestamps: {path}")
            continue

        return {
            "id": f"local:{path.name}",
            "trackName": track.get("title", ""),
            "artistName": track.get("artist", ""),
            "albumName": track.get("album", ""),
            "duration": track.get("duration", 0),
            "instrumental": False,
            "plainLyrics": None,
            "syncedLyrics": synced,
            "source": "local",
            "localPath": str(path),
        }

    return None

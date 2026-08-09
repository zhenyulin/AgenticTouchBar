"""--diagnose and --clear-current: interactive troubleshooting for whatever
track is playing right now."""

from __future__ import annotations

import json

from . import config
from .apple_music import read_apple_music
from .cache import cache_path, lock_path
from .matching import candidate_score, choose_candidate
from .metadata import local_title_variants, metadata_variants, track_cache_key
from .providers.apple_cache import apple_cache_record
from .providers.local import local_lyrics_record
from .providers.lrcapi import collect_lrcapi_candidates
from .providers.lrclib import collect_search_candidates


def diagnose_current() -> int:
    try:
        track = read_apple_music()
    except Exception as exc:
        print(f"Apple Music error: {exc}")
        return 1

    if track.get("state") in {"not_running", "stopped"}:
        print(f"Music state: {track.get('state')}")
        return 1

    print(json.dumps(track, ensure_ascii=False, indent=2))
    print(f"cache key: {track_cache_key(track)}")
    print(f"title variants: {metadata_variants(track.get('title', ''))}")
    print(f"artist variants: {metadata_variants(track.get('artist', ''))}")
    print(f"local lyrics directory: {config.LOCAL_LYRICS_DIR}")
    local = local_lyrics_record(track)
    if local is not None:
        print(f"LOCAL LRC: {local.get('localPath')}")
        return 0
    print("No matching local synchronized LRC.")
    print(f"Apple Music lyrics cache: {config.APPLE_MUSIC_CACHE_DB}")
    apple_cached = apple_cache_record(track)
    if apple_cached is not None:
        print(f"APPLE CACHE: {apple_cached.get('id')}")
        return 0
    print("No matching cached Apple Music TTML.")
    print(f"LrcAPI enabled: {config.ENABLE_LRCAPI}")
    if config.ENABLE_LRCAPI:
        diagnostic_titles = local_title_variants(track.get("title", ""))[:4]
        diagnostic_artists = metadata_variants(track.get("artist", ""))[:6]
        print(f"LrcAPI title variants: {diagnostic_titles}")
        print(f"LrcAPI artist variants: {diagnostic_artists}")
        print(
            "LrcAPI limits: "
            f"{config.LRCAPI_MAX_ADVANCE_QUERIES} advance + "
            f"{config.LRCAPI_MAX_SINGLE_QUERIES} single, "
            f"{config.LRCAPI_TIMEOUT_SECONDS:g}s timeout each"
        )
        print("Searching token-free Chinese providers through LrcAPI …")
        try:
            lrcapi_candidates = collect_lrcapi_candidates(track)
            if lrcapi_candidates:
                ranked_lrcapi = sorted(
                    (
                        (candidate_score(track, item), item)
                        for item in lrcapi_candidates
                    ),
                    key=lambda pair: pair[0],
                    reverse=True,
                )
                for score, item in ranked_lrcapi[:10]:
                    print(
                        f"{score:.3f}  lrcapi  "
                        f"{item.get('artistName')} — {item.get('trackName')} "
                        f"[source={item.get('source')}, id={item.get('providerId')}, "
                        f"query={item.get('sourceQuery')}]"
                    )
                selected_lrcapi = choose_candidate(track, lrcapi_candidates)
                if selected_lrcapi is not None:
                    print(
                        "SELECTED LRCAPI: "
                        f"{selected_lrcapi.get('artistName')} — "
                        f"{selected_lrcapi.get('trackName')}"
                    )
                    return 0
            else:
                print("No synchronized LrcAPI candidates.")
        except Exception as exc:
            print(f"LrcAPI error: {exc}")

    print("Searching LRCLIB synchronously …")

    try:
        candidates = collect_search_candidates(track)
    except Exception as exc:
        print(f"LRCLIB search error: {exc}")
        return 1

    ranked = sorted(
        ((candidate_score(track, item), item) for item in candidates),
        key=lambda pair: pair[0],
        reverse=True,
    )
    if not ranked:
        print("No LRCLIB candidates returned.")
        return 2

    for score, item in ranked[:10]:
        synced = "synced" if item.get("syncedLyrics") else "plain-only"
        print(
            f"{score:6.3f}  {synced:10}  "
            f"{item.get('artistName', '')} — {item.get('trackName', '')}  "
            f"[{float(item.get('duration', 0) or 0):.1f}s]"
        )

    selected = choose_candidate(track, candidates)
    if selected:
        print(
            "SELECTED: "
            f"{selected.get('artistName', '')} — {selected.get('trackName', '')}"
        )
        return 0
    print("Candidates existed, but none passed synchronized-lyrics matching.")
    return 2


def clear_current_cache() -> int:
    try:
        track = read_apple_music()
        key = track_cache_key(track)
        for path in (cache_path(key), lock_path(key)):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        print(f"Cleared current track cache: {key}")
        return 0
    except Exception as exc:
        print(f"Could not clear current cache: {exc}")
        return 1

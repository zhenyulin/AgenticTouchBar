#!/usr/bin/env python3
"""BetterTouchTool Touch Bar lyrics widget for Apple Music.

Apple Music supplies track metadata and playback position through AppleScript.
A token-free Chinese lyrics aggregator (LrcAPI) and LRCLIB supply synchronized LRC lyrics, which are cached locally.

The normal widget path never blocks on the network: a cache miss starts a
background fetch and immediately returns a status string to BetterTouchTool.
"""

from __future__ import annotations

import base64
import bisect
import difflib
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

try:
    from opencc import OpenCC  # type: ignore
except ImportError:
    OpenCC = None  # type: ignore

_OPENCC_CONVERTERS: list[Any] = []
if OpenCC is not None:
    try:
        _OPENCC_CONVERTERS = [OpenCC("t2s"), OpenCC("s2t")]
    except Exception:
        _OPENCC_CONVERTERS = []

# ---- User-tunable defaults -------------------------------------------------
VIEWPORT_WIDTH = int(os.environ.get("BTT_LYRICS_WIDTH", "42"))
SYNC_OFFSET_SECONDS = float(os.environ.get("BTT_LYRICS_OFFSET", "0.0"))
SCROLL_LONG_LINES = os.environ.get("BTT_LYRICS_SCROLL", "1") not in {
    "0",
    "false",
    "False",
}
SCROLL_DELAY_SECONDS = float(os.environ.get("BTT_LYRICS_SCROLL_DELAY", "0.8"))
SCROLL_CELLS_PER_SECOND = float(os.environ.get("BTT_LYRICS_SCROLL_RATE", "5.0"))
NETWORK_TIMEOUT_SECONDS = float(os.environ.get("BTT_LYRICS_NETWORK_TIMEOUT", "5.0"))
APPLE_MUSIC_TIMEOUT_SECONDS = float(
    os.environ.get("BTT_LYRICS_APPLE_MUSIC_TIMEOUT", "1.5")
)

CACHE_DIR = Path.home() / "Library" / "Caches" / "BTTNowPlayingLyrics"
WIDGET_LOCK_PATH = CACHE_DIR / "widget.lock"
LRCLIB_API_BASE = "https://lrclib.net/api"
LRCAPI_API_BASE = "https://api.lrc.cx/api/v1/lyrics"
ENABLE_LRCAPI = os.environ.get("BTT_LYRICS_LRCAPI", "1") not in {"0", "false", "False"}
LRCAPI_TIMEOUT_SECONDS = float(os.environ.get("BTT_LYRICS_LRCAPI_TIMEOUT", "2.5"))
LRCAPI_MAX_ADVANCE_QUERIES = int(os.environ.get("BTT_LYRICS_LRCAPI_MAX_ADVANCE", "4"))
LRCAPI_MAX_SINGLE_QUERIES = int(os.environ.get("BTT_LYRICS_LRCAPI_MAX_SINGLE", "2"))
LRCLIB_MAX_SEARCH_QUERIES = int(os.environ.get("BTT_LYRICS_LRCLIB_MAX_SEARCH", "7"))
FETCH_TIMEOUT_SECONDS = float(os.environ.get("BTT_LYRICS_FETCH_TIMEOUT", "45"))
USER_AGENT = "BTT-NowPlaying-Lyrics/6.0 (personal macOS Touch Bar widget)"
LOCK_MAX_AGE_SECONDS = max(FETCH_TIMEOUT_SECONDS + 15.0, 30.0)
WIDGET_LOCK_MAX_AGE_SECONDS = max(APPLE_MUSIC_TIMEOUT_SECONDS * 4.0, 3.0)
RETRY_NETWORK_ERROR_SECONDS = 300
RETRY_NOT_FOUND_SECONDS = 6 * 60 * 60
MATCHER_VERSION = 6

# Metadata aliases commonly used by streaming catalogs and community lyric
# databases. Add your own groups in lyrics_aliases.json next to this script.
BUILTIN_ALIAS_GROUPS = [
    ["張懸", "张悬", "Deserts Chang", "安溥", "Anpu"],
    [
        "银河快递",
        "銀河快遞",
        "Galaxy Express",
        "银河快递(Galaxy Express)",
        "銀河快遞(Galaxy Express)",
    ],
]
ALIASES_PATH = Path(
    os.environ.get(
        "BTT_LYRICS_ALIASES",
        str(Path(__file__).resolve().with_name("lyrics_aliases.json")),
    )
)

LOCAL_LYRICS_DIR = Path(
    os.environ.get(
        "BTT_LYRICS_LOCAL_DIR",
        str(Path(__file__).resolve().with_name("lyrics")),
    )
)

UNIT_SEPARATOR = "\x1f"
TIMESTAMP_RE = re.compile(r"\[(\d{1,3}):(\d{2}(?:\.\d{1,3})?)\]")
ENHANCED_TIMESTAMP_RE = re.compile(r"<\d{1,3}:\d{2}(?:\.\d{1,3})?>")
OFFSET_RE = re.compile(r"\[offset:([+-]?\d+)\]", re.IGNORECASE)

APPLE_MUSIC_SCRIPT = r"""
tell application "Music"
    set currentState to (player state as text)
    if currentState is "stopped" then return currentState

    set currentTrack to current track
    set sep to ASCII character 31

    try
        set trackName to (name of currentTrack) as text
    on error
        set trackName to ""
    end try

    try
        set artistName to (artist of currentTrack) as text
    on error
        set artistName to ""
    end try

    try
        set albumName to (album of currentTrack) as text
    on error
        set albumName to ""
    end try

    try
        set trackDuration to (duration of currentTrack) as text
    on error
        set trackDuration to "0"
    end try

    try
        set currentPosition to (player position) as text
    on error
        set currentPosition to "0"
    end try

    return currentState & sep & trackName & sep & artistName & sep & albumName & sep & trackDuration & sep & currentPosition
end tell
"""


def emit(text: str) -> None:
    """Print exactly one compact line for BetterTouchTool."""
    text = " ".join(str(text).replace("\r", " ").replace("\n", " ").split())
    print(text or "♪")


def log_error(message: str) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with (CACHE_DIR / "error.log").open("a", encoding="utf-8") as handle:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            handle.write(f"[{stamp}] {message}\n")
    except Exception:
        pass


def music_is_running() -> bool:
    try:
        result = subprocess.run(
            ["/usr/bin/pgrep", "-x", "Music"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=0.25,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def read_apple_music() -> dict[str, Any]:
    if not music_is_running():
        return {"state": "not_running"}

    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", APPLE_MUSIC_SCRIPT],
            capture_output=True,
            text=True,
            timeout=APPLE_MUSIC_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Apple Music query timed out") from exc

    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "-1743" in stderr or "not authorized" in stderr.lower():
            raise PermissionError(
                "Allow BetterTouchTool to control Music in System Settings → Privacy & Security → Automation"
            )
        raise RuntimeError(stderr or "osascript could not read Apple Music")

    raw = result.stdout.strip()
    if raw in {"stopped", ""}:
        return {"state": "stopped"}

    fields = raw.split(UNIT_SEPARATOR)
    if len(fields) != 6:
        raise RuntimeError(f"Unexpected Apple Music response: {raw!r}")

    state, title, artist, album, duration, position = fields
    try:
        duration_value = float(duration)
    except ValueError:
        duration_value = 0.0
    try:
        position_value = float(position)
    except ValueError:
        position_value = 0.0

    return {
        "state": state,
        "title": title.strip(),
        "artist": artist.strip(),
        "album": album.strip(),
        "duration": max(duration_value, 0.0),
        "position": max(position_value, 0.0),
    }


VERSION_MARKERS = (
    "remaster",
    "remastered",
    "version",
    "edit",
    "mix",
    "live",
    "acoustic",
    "demo",
    "mono",
    "stereo",
    "deluxe",
    "bonus",
    "radio",
    "single",
    "現場",
    "现场",
    "演唱會",
    "演唱会",
    "錄音室",
    "录音室",
    "重製",
    "重制",
    "重新錄製",
    "重新录制",
    "專輯版",
    "专辑版",
    "單曲版",
    "单曲版",
)


def strip_version_annotations(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip()

    def keep_or_remove(match: re.Match[str]) -> str:
        body = match.group(1).casefold()
        return (
            "" if any(marker in body for marker in VERSION_MARKERS) else match.group(0)
        )

    value = re.sub(r"\(([^)]*)\)", keep_or_remove, value)
    value = re.sub(r"\[([^]]*)\]", keep_or_remove, value)
    value = re.sub(r"\b(?:feat|featuring|ft)\.?\s+.*$", "", value, flags=re.IGNORECASE)
    return " ".join(value.split())


def normalized(value: str) -> str:
    value = strip_version_annotations(value).casefold()
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def load_alias_groups() -> list[list[str]]:
    groups = [list(group) for group in BUILTIN_ALIAS_GROUPS]
    try:
        with ALIASES_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            for key, values in payload.items():
                if isinstance(values, str):
                    values = [values]
                if isinstance(values, list):
                    groups.append([str(key), *(str(item) for item in values)])
        elif isinstance(payload, list):
            for values in payload:
                if isinstance(values, list):
                    groups.append([str(item) for item in values])
    except FileNotFoundError:
        pass
    except Exception as exc:
        log_error(f"Could not read aliases from {ALIASES_PATH}: {exc}")
    return groups


def basic_metadata_variants(value: str) -> list[str]:
    base = value.strip()
    stripped = strip_version_annotations(base)
    variants = {item for item in (base, stripped) if item}
    for converter in _OPENCC_CONVERTERS:
        for item in list(variants):
            try:
                converted = converter.convert(item).strip()
                if converted:
                    variants.add(converted)
            except Exception:
                pass
    return sorted(variants, key=lambda item: (item != base, len(item), item.casefold()))


def metadata_variants(value: str) -> list[str]:
    base_variants = basic_metadata_variants(value)
    variants = set(base_variants)
    needles = {normalized(item) for item in base_variants}
    for group in load_alias_groups():
        normalized_group = {normalized(item) for item in group}
        if needles & normalized_group:
            variants.update(item for item in group if item)
    base = value.strip()
    return sorted(variants, key=lambda item: (item != base, len(item), item.casefold()))


def aliases_equivalent(left: str, right: str) -> bool:
    left_forms = {normalized(item) for item in basic_metadata_variants(left)}
    right_forms = {normalized(item) for item in basic_metadata_variants(right)}
    if left_forms & right_forms:
        return True
    for group in load_alias_groups():
        normalized_group = {normalized(item) for item in group}
        if left_forms & normalized_group and right_forms & normalized_group:
            return True
    return False


def similarity(left: str, right: str) -> float:
    return difflib.SequenceMatcher(None, normalized(left), normalized(right)).ratio()


def best_similarity(left: str, right: str) -> float:
    if not left.strip() or not right.strip():
        return 0.0
    if aliases_equivalent(left, right):
        return 1.0
    return max(
        similarity(left_variant, right_variant)
        for left_variant in basic_metadata_variants(left)
        for right_variant in basic_metadata_variants(right)
    )


def track_cache_key(track: dict[str, Any]) -> str:
    identity = {
        "title": normalized(track.get("title", "")),
        "artist": normalized(track.get("artist", "")),
        "album": normalized(track.get("album", "")),
        "duration": round(float(track.get("duration", 0))),
        "matcher_version": MATCHER_VERSION,
    }
    payload = json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def lock_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.lock"


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".tmp.{os.getpid()}")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    os.replace(temporary, path)


def read_cache(key: str) -> dict[str, Any] | None:
    path = cache_path(key)
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        log_error(f"Could not read cache {path}: {exc}")
        try:
            path.unlink()
        except OSError:
            pass
        return None


def lrclib_api_request(endpoint: str, params: dict[str, Any]) -> Any:
    clean_params = {
        key: value for key, value in params.items() if value not in {None, ""}
    }
    url = f"{LRCLIB_API_BASE}/{endpoint}?{urllib.parse.urlencode(clean_params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(
            request, timeout=NETWORK_TIMEOUT_SECONDS
        ) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise RuntimeError(f"LRCLIB returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach LRCLIB: {exc.reason}") from exc


def lrcapi_request(params: dict[str, Any]) -> Any:
    """Query the public token-free LrcAPI advance endpoint."""
    clean_params = {
        key: value for key, value in params.items() if value not in {None, ""}
    }
    url = f"{LRCAPI_API_BASE}/advance?{urllib.parse.urlencode(clean_params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(
            request, timeout=LRCAPI_TIMEOUT_SECONDS
        ) as response:
            payload = response.read()
        if not payload:
            return []
        return json.loads(payload.decode("utf-8-sig"))
    except urllib.error.HTTPError as exc:
        if exc.code in {404, 422}:
            return []
        raise RuntimeError(f"LrcAPI returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach LrcAPI: {exc.reason}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("LrcAPI returned an invalid response") from exc


def lrcapi_single_request(params: dict[str, Any]) -> str:
    """Query LrcAPI's single-result endpoint, which returns raw LRC text."""
    clean_params = {
        key: value for key, value in params.items() if value not in {None, ""}
    }
    url = f"{LRCAPI_API_BASE}/single?{urllib.parse.urlencode(clean_params)}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/plain, text/html"},
    )
    try:
        with urllib.request.urlopen(
            request, timeout=LRCAPI_TIMEOUT_SECONDS
        ) as response:
            payload = response.read()
        return payload.decode("utf-8-sig", errors="replace").strip()
    except urllib.error.HTTPError as exc:
        if exc.code in {404, 422}:
            return ""
        raise RuntimeError(f"LrcAPI single endpoint returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Could not reach LrcAPI single endpoint: {exc.reason}"
        ) from exc


def adapt_lrcapi_candidate(item: dict[str, Any]) -> dict[str, Any] | None:
    lyrics = str(item.get("lyrics") or "")
    if not TIMESTAMP_RE.search(lyrics):
        return None
    provider_id = str(
        item.get("id") or hashlib.sha256(lyrics.encode("utf-8")).hexdigest()[:20]
    )
    return {
        "id": f"lrcapi:{provider_id}",
        "providerId": provider_id,
        "source": "lrcapi",
        "trackName": str(item.get("title") or ""),
        "artistName": str(item.get("artist") or ""),
        "albumName": str(item.get("album") or ""),
        "duration": 0,
        "instrumental": False,
        "plainLyrics": None,
        "syncedLyrics": lyrics,
    }


def collect_lrcapi_candidates(track: dict[str, Any]) -> list[dict[str, Any]]:
    """Search Chinese providers through LrcAPI without an API token."""
    if not ENABLE_LRCAPI:
        return []

    titles = local_title_variants(track.get("title", ""))[:4]
    artists = metadata_variants(track.get("artist", ""))[:6]
    album_variants = local_title_variants(track.get("album", ""))[:3]
    queries: list[dict[str, str]] = []
    query_signatures: set[tuple[tuple[str, str], ...]] = set()

    def add_query(title: str, artist: str = "", album_name: str = "") -> None:
        query = {"title": title, "artist": artist, "album": album_name}
        signature = tuple(query.items())
        if title and signature not in query_signatures:
            query_signatures.add(signature)
            queries.append(query)

    if not titles:
        return []

    original_title = titles[0]
    stripped_title = titles[1] if len(titles) > 1 else original_title
    original_artist = artists[0] if artists else ""
    chinese_artists = [
        artist
        for artist in artists
        if any("\u3400" <= char <= "\u9fff" for char in artist)
    ]
    preferred_chinese_artist = (
        chinese_artists[0] if chinese_artists else original_artist
    )
    original_album = album_variants[0] if album_variants else ""
    stripped_album = album_variants[1] if len(album_variants) > 1 else original_album

    # High-value combinations first. In particular, pair a subtitle-free
    # Chinese title with the Chinese artist alias; v4 did not test this pair.
    add_query(original_title, original_artist, original_album)
    add_query(stripped_title, preferred_chinese_artist, stripped_album)
    add_query(stripped_title, preferred_chinese_artist)
    add_query(original_title, preferred_chinese_artist)
    add_query(stripped_title, original_artist)
    add_query(original_title, original_artist)

    # Add a small title × artist matrix, while keeping network work bounded.
    for title in titles[:3]:
        for artist in artists[:4]:
            add_query(title, artist)
    add_query(stripped_title)

    attempted = queries[: max(LRCAPI_MAX_ADVANCE_QUERIES, 0)]
    collected: list[dict[str, Any]] = []
    seen: set[str] = set()
    errors: list[str] = []

    for query in attempted:
        try:
            payload = lrcapi_request(query)
        except Exception as exc:
            errors.append(str(exc))
            continue
        if not isinstance(payload, list):
            continue
        for raw in payload:
            if not isinstance(raw, dict):
                continue
            candidate = adapt_lrcapi_candidate(raw)
            if candidate is None:
                continue
            candidate["sourceQuery"] = query
            identity = str(candidate.get("id"))
            if identity not in seen:
                seen.add(identity)
                collected.append(candidate)

    # The documented /single endpoint sometimes succeeds when /advance
    # returns an empty candidate array. Restrict it to strong title+artist
    # queries to reduce false matches.
    if not collected:
        single_queries = [query for query in queries if query.get("artist")][
            : max(LRCAPI_MAX_SINGLE_QUERIES, 0)
        ]
        for query in single_queries:
            try:
                lyrics = lrcapi_single_request(query)
            except Exception as exc:
                errors.append(str(exc))
                continue
            if not TIMESTAMP_RE.search(lyrics):
                continue
            provider_id = hashlib.sha256(
                (json.dumps(query, ensure_ascii=False, sort_keys=True) + lyrics).encode(
                    "utf-8"
                )
            ).hexdigest()[:20]
            collected.append(
                {
                    "id": f"lrcapi-single:{provider_id}",
                    "providerId": provider_id,
                    "source": "lrcapi-single",
                    "trackName": query["title"],
                    "artistName": query["artist"],
                    "albumName": query.get("album", ""),
                    "duration": track.get("duration", 0),
                    "instrumental": False,
                    "plainLyrics": None,
                    "syncedLyrics": lyrics,
                    "sourceQuery": query,
                }
            )
            break

    if not collected and attempted and len(errors) >= len(attempted):
        raise RuntimeError(errors[0])
    return collected


def choose_lrcapi_candidate(track: dict[str, Any]) -> dict[str, Any] | None:
    return choose_candidate(track, collect_lrcapi_candidates(track))


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


def collect_search_candidates(track: dict[str, Any]) -> list[dict[str, Any]]:
    title_variants = metadata_variants(track.get("title", ""))[:4]
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

    for query in queries[: max(LRCLIB_MAX_SEARCH_QUERIES, 0)]:
        add_results(lrclib_api_request("search", query))

    return collected


def local_title_variants(value: str) -> list[str]:
    """Return filename-friendly title variants, including subtitle-free forms."""
    variants = set(metadata_variants(value))
    for item in list(variants):
        # Also accept trailing subtitles such as "(Chill)" / "（Chill）".
        stripped = re.sub(r"\s*[\(（][^\(\)（）]+[\)）]\s*$", "", item).strip()
        if stripped:
            variants.add(stripped)
    return sorted(
        variants, key=lambda item: (item != value.strip(), len(item), item.casefold())
    )


def local_lyrics_record(track: dict[str, Any]) -> dict[str, Any] | None:
    """Load a synchronized .lrc from the local lyrics directory when available."""
    if not LOCAL_LYRICS_DIR.is_dir():
        return None

    titles = local_title_variants(track.get("title", ""))
    artists = metadata_variants(track.get("artist", ""))
    candidate_paths: list[Path] = []
    seen: set[Path] = set()

    # Prefer deterministic names: "Artist - Title.lrc" or "Title.lrc".
    for title in titles:
        for filename in (f"{title}.lrc",):
            path = LOCAL_LYRICS_DIR / filename
            if path not in seen:
                seen.add(path)
                candidate_paths.append(path)
        for artist in artists:
            for separator in (" - ", " — "):
                path = LOCAL_LYRICS_DIR / f"{artist}{separator}{title}.lrc"
                if path not in seen:
                    seen.add(path)
                    candidate_paths.append(path)

    # Then scan the small local directory for equivalent simplified/traditional
    # metadata or slightly different punctuation.
    title_forms = {normalized(item) for item in titles if normalized(item)}
    artist_forms = {normalized(item) for item in artists if normalized(item)}
    try:
        for path in LOCAL_LYRICS_DIR.glob("*.lrc"):
            stem = normalized(path.stem)
            title_match = any(form and form in stem for form in title_forms)
            artist_match = not artist_forms or any(
                form and form in stem for form in artist_forms
            )
            if title_match and artist_match and path not in seen:
                seen.add(path)
                candidate_paths.append(path)
    except OSError as exc:
        log_error(f"Could not scan local lyrics directory {LOCAL_LYRICS_DIR}: {exc}")

    for path in candidate_paths:
        try:
            synced = path.read_text(encoding="utf-8-sig")
        except FileNotFoundError:
            continue
        except OSError as exc:
            log_error(f"Could not read local LRC {path}: {exc}")
            continue

        if not TIMESTAMP_RE.search(synced):
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


def fetch_lyrics_record(track: dict[str, Any]) -> dict[str, Any] | None:
    # Provider order: user-maintained local LRC, token-free Chinese sources,
    # then the open LRCLIB database.
    local = local_lyrics_record(track)
    if local is not None:
        return local

    try:
        lrcapi = choose_lrcapi_candidate(track)
    except Exception as exc:
        log_error(f"LrcAPI lookup failed; trying LRCLIB: {exc}")
        lrcapi = None
    if lrcapi is not None:
        return lrcapi

    title_variants = metadata_variants(track.get("title", ""))[:3]
    artist_variants = metadata_variants(track.get("artist", ""))[:5]
    duration = round(float(track.get("duration", 0))) or None

    # Try the original catalog metadata once; flexible searches handle aliases.
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
    )
    if isinstance(exact, dict) and (
        exact.get("syncedLyrics") or exact.get("instrumental")
    ):
        exact = dict(exact)
        exact.setdefault("source", "lrclib")
        exact.setdefault("providerId", exact.get("id"))
        return exact

    selected = choose_candidate(track, collect_search_candidates(track))
    if selected is not None:
        selected = dict(selected)
        selected.setdefault("source", "lrclib")
        selected.setdefault("providerId", selected.get("id"))
    return selected


def background_fetch(key: str, track: dict[str, Any]) -> None:
    lock = lock_path(key)
    try:
        try:
            record = fetch_lyrics_record(track)
            now = time.time()
            if record is None:
                payload = {
                    "status": "not_found",
                    "fetched_at": now,
                    "retry_after": now + RETRY_NOT_FOUND_SECONDS,
                }
            elif record.get("instrumental"):
                payload = {
                    "status": "instrumental",
                    "fetched_at": now,
                    "retry_after": now + RETRY_NOT_FOUND_SECONDS,
                    "record": record,
                }
            else:
                record = dict(record)
                record["parsedLines"] = parse_lrc(record.get("syncedLyrics") or "")
                payload = {
                    "status": "ok",
                    "fetched_at": now,
                    "record": record,
                }
            atomic_write_json(cache_path(key), payload)
        except Exception as exc:
            now = time.time()
            log_error(
                f"Lyrics fetch failed for {track.get('artist')} — {track.get('title')}: {exc}"
            )
            atomic_write_json(
                cache_path(key),
                {
                    "status": "network_error",
                    "message": str(exc),
                    "fetched_at": now,
                    "retry_after": now + RETRY_NETWORK_ERROR_SECONDS,
                },
            )
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def start_background_fetch(key: str, track: dict[str, Any]) -> bool:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    lock = lock_path(key)

    try:
        if lock.exists() and time.time() - lock.stat().st_mtime > LOCK_MAX_AGE_SECONDS:
            lock.unlink()
    except OSError:
        pass

    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
    except FileExistsError:
        return False

    payload = base64.urlsafe_b64encode(
        json.dumps(track, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    try:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--fetch", key, payload],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
        return True
    except Exception:
        try:
            lock.unlink()
        except OSError:
            pass
        raise


def acquire_widget_lock() -> bool:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if (
            WIDGET_LOCK_PATH.exists()
            and time.time() - WIDGET_LOCK_PATH.stat().st_mtime
            > WIDGET_LOCK_MAX_AGE_SECONDS
        ):
            WIDGET_LOCK_PATH.unlink()
    except OSError:
        pass

    try:
        descriptor = os.open(
            WIDGET_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        return True
    except FileExistsError:
        return False


def release_widget_lock() -> None:
    try:
        if WIDGET_LOCK_PATH.read_text(encoding="utf-8") == str(os.getpid()):
            WIDGET_LOCK_PATH.unlink()
    except (FileNotFoundError, OSError):
        pass


def parse_lrc(synced_lyrics: str) -> list[tuple[float, str]]:
    offset_match = OFFSET_RE.search(synced_lyrics)
    offset_seconds = float(offset_match.group(1)) / 1000.0 if offset_match else 0.0
    parsed: list[tuple[float, str]] = []

    for raw_line in synced_lyrics.splitlines():
        timestamps = TIMESTAMP_RE.findall(raw_line)
        if not timestamps:
            continue
        text = TIMESTAMP_RE.sub("", raw_line)
        text = ENHANCED_TIMESTAMP_RE.sub("", text).strip()
        if not text:
            continue
        for minutes, seconds in timestamps:
            timestamp = int(minutes) * 60 + float(seconds) + offset_seconds
            parsed.append((max(timestamp, 0.0), text))

    parsed.sort(key=lambda item: item[0])
    deduplicated: list[tuple[float, str]] = []
    for timestamp, text in parsed:
        if deduplicated and abs(deduplicated[-1][0] - timestamp) < 0.001:
            deduplicated[-1] = (timestamp, text)
        else:
            deduplicated.append((timestamp, text))
    return deduplicated


def character_width(character: str) -> int:
    if unicodedata.combining(character):
        return 0
    return 2 if unicodedata.east_asian_width(character) in {"W", "F", "A"} else 1


def display_width(text: str) -> int:
    return sum(character_width(character) for character in text)


def crop_cells(text: str, start: int, width: int) -> str:
    current = 0
    used = 0
    output: list[str] = []
    for character in text:
        char_width = character_width(character)
        next_position = current + char_width
        if next_position <= start:
            current = next_position
            continue
        if current < start < next_position:
            current = next_position
            continue
        if used + char_width > width:
            break
        output.append(character)
        used += char_width
        current = next_position
    return "".join(output).strip()


def marquee(text: str, elapsed: float, width: int) -> str:
    if width <= 0 or display_width(text) <= width:
        return text
    if not SCROLL_LONG_LINES:
        return crop_cells(text, 0, max(width - 1, 1)) + "…"

    maximum_offset = max(display_width(text) - width, 0)
    moving_time = max(0.0, elapsed - SCROLL_DELAY_SECONDS)
    offset = min(int(moving_time * SCROLL_CELLS_PER_SECOND), maximum_offset)
    return crop_cells(text, offset, width)


def current_lyric_line(
    lines: list[tuple[float, str]], position: float
) -> tuple[str | None, float]:
    if not lines:
        return None, 0.0
    timestamps = [item[0] for item in lines]
    index = bisect.bisect_right(timestamps, position) - 1
    if index < 0:
        return None, 0.0
    timestamp, text = lines[index]
    return text, max(position - timestamp, 0.0)


def render_widget(track: dict[str, Any], cached: dict[str, Any] | None) -> str:
    state = track.get("state", "")
    title = track.get("title", "") or "Apple Music"

    if cached is None:
        return f"⌛ {title}"

    status = cached.get("status")
    if status == "instrumental":
        return "♪ Instrumental"
    if status == "not_found":
        return "♪ No synced lyrics"
    if status == "network_error":
        return "⚠ Lyrics network error"
    if status != "ok":
        return "♪ Lyrics unavailable"

    record = cached.get("record") or {}
    synced_lyrics = record.get("syncedLyrics") or ""
    lines = record.get("parsedLines") or parse_lrc(synced_lyrics)
    position = float(track.get("position", 0.0)) + SYNC_OFFSET_SECONDS
    lyric, elapsed = current_lyric_line(lines, position)

    prefix = "Ⅱ " if state == "paused" else "♪ "
    if lyric is None:
        return prefix + title
    available_width = max(VIEWPORT_WIDTH - display_width(prefix), 8)
    return prefix + marquee(lyric, elapsed, available_width)


def widget_main() -> int:
    if not acquire_widget_lock():
        emit("♪")
        return 0

    try:
        try:
            track = read_apple_music()
        except PermissionError:
            emit("⚠ Allow BTT → Music")
            return 0
        except Exception as exc:
            log_error(str(exc))
            emit("⚠ Apple Music error")
            return 0

        state = track.get("state")
        if state in {"not_running", "stopped"} or not track.get("title"):
            emit("♪")
            return 0

        key = track_cache_key(track)
        cached = read_cache(key)
        now = time.time()

        if cached is None:
            try:
                start_background_fetch(key, track)
            except Exception as exc:
                log_error(f"Could not start background fetch: {exc}")
            emit(render_widget(track, None))
            return 0

        retry_after = float(cached.get("retry_after", 0) or 0)
        if retry_after and now >= retry_after:
            try:
                cache_path(key).unlink()
            except OSError:
                pass
            try:
                start_background_fetch(key, track)
            except Exception as exc:
                log_error(f"Could not retry background fetch: {exc}")
            emit(render_widget(track, None))
            return 0

        emit(render_widget(track, cached))
        return 0
    finally:
        release_widget_lock()


def fetch_mode(arguments: list[str]) -> int:
    if len(arguments) != 2:
        return 2
    key, encoded_payload = arguments
    try:
        track = json.loads(
            base64.urlsafe_b64decode(encoded_payload.encode("ascii")).decode("utf-8")
        )

        def stop_fetch(_signum: int, _frame: Any) -> None:
            raise TimeoutError(f"Lyrics fetch exceeded {FETCH_TIMEOUT_SECONDS:g}s")

        signal.signal(signal.SIGALRM, stop_fetch)
        signal.setitimer(signal.ITIMER_REAL, max(FETCH_TIMEOUT_SECONDS, 1.0))
        try:
            background_fetch(key, track)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
        return 0
    except Exception as exc:
        log_error(f"Background fetch process failed: {exc}")
        try:
            lock_path(key).unlink()
        except OSError:
            pass
        return 1


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
    print(f"local lyrics directory: {LOCAL_LYRICS_DIR}")
    local = local_lyrics_record(track)
    if local is not None:
        print(f"LOCAL LRC: {local.get('localPath')}")
        return 0
    print("No matching local synchronized LRC.")
    print(f"LrcAPI enabled: {ENABLE_LRCAPI}")
    if ENABLE_LRCAPI:
        diagnostic_titles = local_title_variants(track.get("title", ""))[:4]
        diagnostic_artists = metadata_variants(track.get("artist", ""))[:6]
        print(f"LrcAPI title variants: {diagnostic_titles}")
        print(f"LrcAPI artist variants: {diagnostic_artists}")
        print(
            "LrcAPI limits: "
            f"{LRCAPI_MAX_ADVANCE_QUERIES} advance + "
            f"{LRCAPI_MAX_SINGLE_QUERIES} single, "
            f"{LRCAPI_TIMEOUT_SECONDS:g}s timeout each"
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


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--fetch":
        return fetch_mode(sys.argv[2:])
    if len(sys.argv) >= 2 and sys.argv[1] == "--diagnose":
        return diagnose_current()
    if len(sys.argv) >= 2 and sys.argv[1] == "--clear-current":
        return clear_current_cache()
    return widget_main()


if __name__ == "__main__":
    raise SystemExit(main())

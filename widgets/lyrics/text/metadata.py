"""Normalizing track metadata for the local lyrics cache key."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher
from functools import cache
from typing import Any

from .. import config
from ..runtime.output import log_error

VERSION_MARKERS = (
    "remaster",
    "remastered",
    "version",
    "edit",
    "mix",
    "remix",
    "live",
    "acoustic",
    "unplugged",
    "demo",
    "mono",
    "stereo",
    "deluxe",
    "bonus",
    "radio",
    "single",
    "instrumental",
    "inst",
    "inst.",
    "karaoke",
    "ktv",
    "ktv版",
    "off vocal",
    "backing track",
    "acapella",
    "a cappella",
    "piano",
    "cover",
    "orchestral",
    "伴奏",
    "伴唱",
    "纯音乐",
    "钢琴",
    "无人声",
    "器乐",
    "卡拉ok",
    "翻唱",
    "原唱",
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

# Trailing parenthetical subtitles, e.g. "人生马拉松 (渣打香港马拉松
# 二十周年主题曲)" or "因为爱情 (电影《将爱情进行到底》主题曲)".
_SUBTITLE_RE = re.compile(r"\s*[\(（][^\(\)（）]+[\)）]\s*$")
# Trailing "Title - Artist"-style credit tails. Kept in search variants
# (catalogs index them), trimmed from the comparison key used for matching.
_TRAILING_DASH_RE = re.compile(r"\s*[-—–]\s+.*$")


def _marker_in_body(body: str, marker: str) -> bool:
    """Whether an annotation marker appears inside a bracketed clause.

    ASCII markers match on word boundaries so "cover" does not fire inside
    "Discover" and "inst" does not fire inside "Instructor"; CJK markers
    match as plain substrings so "伴奏" still fires inside "伴奏版".
    """
    if marker.isascii() and marker[0].isalnum():
        return re.search(rf"(?<!\w){re.escape(marker)}(?!\w)", body) is not None
    return marker in body


def strip_version_annotations(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip()

    def keep_or_remove(match: re.Match[str]) -> str:
        body = match.group(1).casefold()
        if any(_marker_in_body(body, marker) for marker in VERSION_MARKERS):
            return ""
        return match.group(0)

    # Feat clauses run first and swallow their opening bracket -- without
    # this, "Song (feat. X)" would lose only the tail and leave "Song (".
    value = re.sub(
        r"\s*\(?\s*\b(?:feat|featuring|ft)\.?\s+[^()]*\)?.*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\(([^)]*)\)", keep_or_remove, value)
    value = re.sub(r"\[([^]]*)\]", keep_or_remove, value)
    value = re.sub(r"\s*[-—–]\s*([^()\[\]]*)$", keep_or_remove, value)
    value = re.sub(r"\s*[-—–]\s*$", "", value)
    return " ".join(value.split())


def comparison_key(value: str) -> str:
    """The form two names must agree on to count as the same identity.

    Everything whose job is describing the recording rather than naming it
    goes: version and instrumental/karaoke annotations (parenthesised,
    bracketed or dash-suffixed), trailing subtitles, feat clauses, and
    "Title - Artist"-style credit tails.
    """
    value = strip_version_annotations(value)
    value = _SUBTITLE_RE.sub("", value)
    value = _TRAILING_DASH_RE.sub("", value)
    return normalized(value)


def strict_identity_match(
    left: str, right: str, *, allow_containment: bool = False
) -> bool:
    """Whether two names are the same identity under strict matching.

    Alias groups and converted scripts count as exact; otherwise the
    annotation-trimmed comparison keys must be equal. With allow_containment
    one key may also appear as a whole whitespace-delimited run inside the
    other -- the "The Beatles" vs "Beatles" case. Containment is deliberately
    not offered for titles: a track titled "Yellow" must never accept
    "Yellow Submarine".
    """
    if not left.strip() or not right.strip():
        return False
    if aliases_equivalent(left, right):
        return True
    left_key = comparison_key(left)
    right_key = comparison_key(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    if not allow_containment:
        return False
    shorter, longer = (
        (left_key, right_key)
        if len(left_key) <= len(right_key)
        else (right_key, left_key)
    )
    return (
        len(shorter) >= 2
        and re.search(rf"(?:^|\s){re.escape(shorter)}(?:\s|$)", longer) is not None
    )


def normalized(value: str) -> str:
    value = strip_version_annotations(value).casefold()
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


@cache
def opencc_converters() -> tuple[Any, ...]:
    try:
        from opencc import OpenCC  # type: ignore[reportMissingImports]

        return OpenCC("t2s"), OpenCC("s2t")
    except Exception:
        return ()


@cache
def load_alias_groups() -> list[list[str]]:
    groups: list[list[str]] = []
    try:
        with config.ALIASES_PATH.open("r", encoding="utf-8") as handle:
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
        log_error(f"Could not read aliases from {config.ALIASES_PATH}: {exc}")
    return groups


def basic_metadata_variants(value: str) -> list[str]:
    base = value.strip()
    stripped = strip_version_annotations(base)
    variants = {item for item in (base, stripped) if item}
    for converter in opencc_converters():
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


def track_title_variants(track: dict[str, Any], *, local: bool = False) -> list[str]:
    title_values = [
        str(track.get(key) or "").strip() for key in ("search_title", "title")
    ]
    variants: set[str] = set()
    variant_builder = local_title_variants if local else metadata_variants
    for value in title_values:
        variants.update(variant_builder(value))
        if not local:
            stripped = _SUBTITLE_RE.sub("", value).strip()
            if stripped:
                variants.update(metadata_variants(stripped))
    # Community catalogs usually keep just the subtitle-free title, so the
    # primary search key is the primary title without its trailing
    # parenthetical -- "人生马拉松" rather than
    # "人生马拉松 (渣打香港马拉松二十周年主题曲)".
    primary = _SUBTITLE_RE.sub("", title_values[0]).strip() or title_values[0]
    return sorted(
        variants,
        key=lambda item: (item != primary, len(item), item.casefold()),
    )


def track_artist_variants(track: dict[str, Any]) -> list[str]:
    artist_values = [
        str(track.get(key) or "").strip() for key in ("search_artist", "artist")
    ]
    variants: set[str] = set()
    for value in artist_values:
        variants.update(metadata_variants(value))
    return sorted(
        variants,
        key=lambda item: (item != artist_values[0], len(item), item.casefold()),
    )


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
    return SequenceMatcher(None, normalized(left), normalized(right)).ratio()


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


def local_title_variants(value: str) -> list[str]:
    variants = set(metadata_variants(value))
    for item in list(variants):
        stripped = _SUBTITLE_RE.sub("", item).strip()
        if stripped:
            variants.add(stripped)
    return sorted(
        variants,
        key=lambda item: (item != value.strip(), len(item), item.casefold()),
    )


def track_cache_key(track: dict[str, Any]) -> str:
    identity = {
        "title": normalized(track.get("title", "")),
        "artist": normalized(track.get("artist", "")),
        "album": normalized(track.get("album", "")),
        "duration": round(float(track.get("duration", 0))),
        "cache_version": config.CACHE_KEY_VERSION,
    }
    payload = json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]

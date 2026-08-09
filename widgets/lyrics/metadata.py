"""Normalizing track metadata for the local lyrics cache key."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher
from functools import cache
from typing import Any

from . import config
from .output import log_error

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


@cache
def opencc_converters() -> tuple[Any, ...]:
    try:
        from opencc import OpenCC  # type: ignore[reportMissingImports]

        return OpenCC("t2s"), OpenCC("s2t")
    except Exception:
        return ()


@cache
def load_alias_groups() -> list[list[str]]:
    groups = [list(group) for group in config.BUILTIN_ALIAS_GROUPS]
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
    return sorted(
        variants,
        key=lambda item: (item != title_values[0], len(item), item.casefold()),
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
        stripped = re.sub(r"\s*[\(（][^\(\)（）]+[\)）]\s*$", "", item).strip()
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

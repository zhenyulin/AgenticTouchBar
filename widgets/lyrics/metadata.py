"""Normalizing track metadata for the local lyrics cache key."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any

from . import config

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

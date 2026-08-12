"""Parsing LRC synchronized-lyrics text into (timestamp, text) lines."""

from __future__ import annotations

import re

from .. import config

# One-line credit tags NetEase/QQ LRCs carry at song start (作词/作曲/词/曲/
# 编曲/制作人/...). They have timestamps and would occupy the widget for the
# whole intro, so drop lines whose text is only such a tag.
CREDIT_LINE_RE = re.compile(
    r"^(?:作词|作曲|编曲|制作人|制作|混音|录音|母带|监制|企划|统筹|出品|发行|封面|OP|SP)"
    r"(?:[：:]\s*|\s)"
    r"|^(?:词|曲)[：:]"
)


def strip_credit_lines(lyrics: str) -> str:
    """Drop timestamped credit-tag lines from a provider LRC."""
    return "\n".join(
        line
        for line in lyrics.splitlines()
        if not CREDIT_LINE_RE.match(config.TIMESTAMP_RE.sub("", line).strip())
    )


def plain_to_lrc(plain_text: str, duration: float) -> str:
    """Synthesize an LRC by spreading plain-text lines across the duration.

    A stopgap for records that only carry plain lyrics: the timings are an
    even approximation, never a sync, so this is a last resort for tracks
    with no timed lyrics anywhere.
    """
    lines = [line.strip() for line in plain_text.splitlines() if line.strip()]
    if not lines or duration <= 0:
        return ""
    step = duration / len(lines)
    return "\n".join(
        f"[{int(index * step) // 60:02d}:{index * step % 60:06.3f}]{line}"
        for index, line in enumerate(lines)
    )


def parse_lrc(synced_lyrics: str) -> list[tuple[float, str]]:
    offset_match = config.OFFSET_RE.search(synced_lyrics)
    offset_seconds = float(offset_match.group(1)) / 1000.0 if offset_match else 0.0
    parsed: list[tuple[float, str]] = []

    for raw_line in synced_lyrics.splitlines():
        timestamps = config.TIMESTAMP_RE.findall(raw_line)
        if not timestamps:
            continue
        text = config.TIMESTAMP_RE.sub("", raw_line)
        text = config.ENHANCED_TIMESTAMP_RE.sub("", text).strip()
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

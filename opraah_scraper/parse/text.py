"""Caption text helpers: hashtags, mentions, dates in accessibility captions."""

from __future__ import annotations

import re
from datetime import datetime, timezone

_HASHTAG_RE = re.compile(r"(?<![\w&/])#([^\s#@.,!?;:()\[\]{}\"'“”‘’<>|\\/]+)")
_MENTION_RE = re.compile(r"(?<![\w.@/])@([A-Za-z0-9._]{1,30})")
_A11Y_DATE_RE = re.compile(r"\bon ([A-Z][a-z]+ \d{1,2}, \d{4})")


def hashtags(caption: str | None) -> list[str]:
    if not caption:
        return []
    return list(dict.fromkeys(h.lower() for h in _HASHTAG_RE.findall(caption)))


def mentions(caption: str | None) -> list[str]:
    if not caption:
        return []
    out = []
    for m in _MENTION_RE.findall(caption):
        h = m.rstrip(".").lower()
        if h and h not in out:
            out.append(h)
    return out


def date_from_accessibility_caption(text: str | None) -> str | None:
    """'Video by X on February 18, 2026.' -> '2026-02-18T00:00:00+00:00' (day precision)."""
    if not text:
        return None
    m = _A11Y_DATE_RE.search(text)
    if not m:
        return None
    try:
        d = datetime.strptime(m.group(1), "%B %d, %Y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return d.isoformat()

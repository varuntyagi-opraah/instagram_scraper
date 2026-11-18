from __future__ import annotations

import json
import re
from datetime import datetime, timezone

SHORTCODE_RE = re.compile(r"instagram\.com/(?:[A-Za-z0-9._]+/)?(?:p|reel|reels|tv)/([A-Za-z0-9_-]{5,})")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    v = value.strip()
    # Graph API uses +0000 rather than +00:00
    v = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", v.replace("Z", "+00:00"))
    dt = datetime.fromisoformat(v)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def from_epoch(ts: int | float | None) -> str | None:
    if ts is None:
        return None
    return iso(datetime.fromtimestamp(int(ts), timezone.utc))


def shortcode_from_url(url: str | None) -> str | None:
    if not url:
        return None
    m = SHORTCODE_RE.search(url)
    return m.group(1) if m else None


def dumps(value) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=isinstance(value, dict))


def loads(value: str | None, default=None):
    if value is None:
        return default
    return json.loads(value)


def int_or_none(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None

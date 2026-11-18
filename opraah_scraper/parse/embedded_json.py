"""Read the JSON that public Instagram pages embed in <script type="application/json"> tags.

Ported from the GamerFleet pilot (tests/fixtures/gamerfleet/collect.py): parse every JSON
script blob, then walk all nested dicts. Matching is by content (e.g. a dict whose `code`
is the shortcode and that has an integer `taken_at`), not by a fixed path, so small layout
changes on Instagram's side don't break extraction.
"""

from __future__ import annotations

import json
import re
from contextlib import suppress
from typing import Any, Iterable, Iterator

_SCRIPT_RE = re.compile(r'<script type="application/json"[^>]*>(.*?)</script>', re.DOTALL)


def script_blobs(html: str) -> list[Any]:
    blobs = []
    for m in _SCRIPT_RE.finditer(html):
        with suppress(json.JSONDecodeError):
            blobs.append(json.loads(m.group(1)))
    return blobs


def query_data(obj: Any) -> Iterator[dict]:
    """Yield `result.data` of each embedded GraphQL result (same rule Instaloader uses)."""
    if isinstance(obj, dict):
        bbox = obj.get("__bbox")
        result = bbox.get("result") if isinstance(bbox, dict) else None
        if isinstance(result, dict) and isinstance(result.get("data"), dict):
            yield result["data"]
        for v in obj.values():
            yield from query_data(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from query_data(v)


def page_data(html: str) -> list[dict]:
    """The data blocks of a saved page, equivalent to InstaloaderContext.get_page_data()."""
    out: list[dict] = []
    for blob in script_blobs(html):
        out.extend(query_data(blob))
    return out


def walk(obj: Any) -> Iterator[dict]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v)


def all_dicts(blocks: Iterable[Any]) -> Iterator[dict]:
    for b in blocks:
        yield from walk(b)

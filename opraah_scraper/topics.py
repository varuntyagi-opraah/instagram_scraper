"""v1 heuristic content themes from captions, hashtags and accessibility captions."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from .config import PROJECT_ROOT

_WORD = re.compile(r"[a-z0-9]+")


def load_taxonomy(path: Path | None = None) -> dict[str, set[str]]:
    custom = path or PROJECT_ROOT / "config" / "topics.json"
    raw = (Path(custom).read_text(encoding="utf-8") if Path(custom).is_file()
           else (Path(__file__).parent / "default_topics.json").read_text(encoding="utf-8"))
    data = json.loads(raw)
    return {k: {w.lower() for w in v} for k, v in data.items() if not k.startswith("_")}


def post_topics(post: dict, taxonomy: dict[str, set[str]]) -> list[str]:
    words = set(_WORD.findall((post.get("caption") or "").lower()))
    words |= {h.lower() for h in post.get("hashtags") or []}
    # Instagram's own description ("May be an image of car and text") is a useful signal.
    words |= set(_WORD.findall((post.get("accessibility_caption") or "").lower()))
    return [t for t, kws in taxonomy.items() if words & kws]


def creator_themes(posts: list[dict], taxonomy: dict[str, set[str]], top: int = 5) -> list[tuple[str, int]]:
    c: Counter = Counter()
    for p in posts:
        c.update(post_topics(p, taxonomy))
    return c.most_common(top)

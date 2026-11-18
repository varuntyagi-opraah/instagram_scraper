"""Shared fixtures: the saved GamerFleet pilot pages and a fake Instaloader context.

The fake context stands in for InstaloaderContext so the real WebClient code (pacing,
error mapping, parsing) runs offline against data captured from Instagram on 17 Nov 2026.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from opraah_scraper import db
from opraah_scraper.budget import Pacer
from opraah_scraper.config import Settings
from opraah_scraper.parse.embedded_json import page_data

FIX = Path(__file__).parent / "fixtures"
GF = FIX / "gamerfleet"
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def gf_posts() -> list[dict]:
    return json.loads((GF / "posts.json").read_text())


@pytest.fixture
def gf_codes() -> list[str]:
    return json.loads((GF / "instaloader-codes.json").read_text())


def media_block(row: dict) -> dict:
    """A post-page data block shaped like Instagram's xig_polaris_media, from a pilot row."""
    ts = int(datetime.fromisoformat(row["date_utc"]).timestamp())
    return {"xig_polaris_media": {
        "code": row["shortcode"], "pk": "9" + str(abs(hash(row["shortcode"])) % 10**12), "taken_at": ts,
        "user": {"username": row["owner"], "pk": "1790371399" if row["owner"] == "gamerfleetog" else "555"},
        "caption": {"text": row["caption"]}, "like_count": row["likes"], "comment_count": row["comments"],
        "coauthor_producers": [{"username": c} for c in row["coauthors"]],
        "usertags": {"in": []},
        "location": {"name": row["location"]} if row["location"] else None,
        "product_type": "clips" if row["type"] == "reel" else "carousel_container",
        "media_type": 2 if row["type"] == "reel" else 8,
        "like_and_view_counts_disabled": False,
    }}


class FakeContext:
    """Minimal InstaloaderContext: get_page_data + doc_id_graphql_query, with error injection."""

    def __init__(self, posts: list[dict], codes: list[str], username: str = "gamerfleetog"):
        self.posts = {p["shortcode"]: p for p in posts}
        self.codes = codes
        self.username = username
        self.calls: list[str] = []
        self.fail: dict[str, Exception] = {}       # path/kind -> exception to raise (once)
        self.fail_always: dict[str, Exception] = {}
        self.interrupt_after: int | None = None

    def _maybe_fail(self, key: str):
        if self.interrupt_after is not None and len(self.calls) > self.interrupt_after:
            raise KeyboardInterrupt
        if key in self.fail_always:
            raise self.fail_always[key]
        if key in self.fail:
            raise self.fail.pop(key)

    def get_page_data(self, path: str):
        self.calls.append(path)
        self._maybe_fail(path)
        if path == f"{self.username}/":
            return page_data((GF / "profile.html").read_text())
        if path == f"{self.username}/reels/":
            return page_data((GF / "reels.html").read_text())
        if path == f"{self.username}/tagged/":
            return page_data((GF / "tagged.html").read_text())
        if path.startswith("p/"):
            code = path.split("/")[1]
            if code not in self.posts:
                import instaloader.exceptions as ie
                raise ie.QueryReturnedNotFoundException("404 Not Found")
            return [media_block(self.posts[code])]
        import instaloader.exceptions as ie
        raise ie.QueryReturnedNotFoundException("404 Not Found")

    def doc_id_graphql_query(self, doc_id, variables, referer=None, web_api=False):
        self.calls.append(f"timeline:{variables.get('after')}")
        self._maybe_fail("timeline")
        start = int(variables["after"].split(":")[1]) if variables["after"].startswith("cursor:") else 12
        batch = self.codes[start:start + 12]
        edges = []
        for c in batch:
            row = self.posts.get(c)
            d = datetime.fromisoformat(row["date_utc"]) if row else NOW
            edges.append({"node": {
                "code": c, "pk": "1", "media_type": 2 if (row or {}).get("type") == "reel" else 8,
                "product_type": "clips" if (row or {}).get("type") == "reel" else "carousel_container",
                "caption": {"text": (row or {}).get("caption") or ""},
                "accessibility_caption": f"Photo by Anshu Bisht on {d.strftime('%B')} {d.day}, {d.year}.",
                "user": {"username": (row or {}).get("owner", self.username), "id": "17841401061997974"}}})
        nxt = start + 12
        return {"data": {"node": {"polaris_ordered_timeline_connection": {
            "edges": edges, "page_info": {"has_next_page": nxt < len(self.codes), "end_cursor": f"cursor:{nxt}"}}}}}


def quiet_pacer(name="web", **kw) -> Pacer:
    sleeps: list[float] = []
    clock = [0.0]

    def sleep(s):
        sleeps.append(s)
        clock[0] += s

    p = Pacer(name, kw.pop("min_interval", 0.0), kw.pop("max_interval", 0.0), kw.pop("hourly_cap", 0),
              kw.pop("backoff_minutes", (15, 30, 60)), sleep=sleep, clock=lambda: clock[0],
              rng=lambda: 0.5, log=lambda m: None, **kw)
    p.sleeps = sleeps
    return p


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path / "data")


@pytest.fixture
def conn(settings):
    c = db.connect(settings.db_path)
    yield c
    c.close()

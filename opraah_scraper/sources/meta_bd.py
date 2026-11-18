"""Meta Business Discovery (Instagram API with Facebook Login): free, official, one call per page.

GET /{app user's IG user id}?fields=business_discovery.username(<handle>){<profile fields>,
    media.limit(50)[.after(<cursor>)]{<media fields>}}

Returns profile counts and up to 50 posts with timestamp, caption, likes, comments and
Reel view_count. It does not return co-authors, tagged users, geotags, paid-partnership
labels, collab posts owned by other accounts, personal (non-professional) accounts or
age-gated accounts. Those gaps are filled by the logged-out web sources.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime

import requests

from ..budget import Outcome, Pacer
from ..metrics import window_satisfied
from ..util import int_or_none, iso, parse_iso, shortcode_from_url
from . import Recorder, null_recorder

PROFILE_FIELDS = "id,username,name,biography,website,followers_count,follows_count,media_count,profile_picture_url"
MEDIA_FIELDS = "id,caption,timestamp,media_type,media_product_type,permalink,like_count,comments_count,view_count"
# Added to the Media endpoint in April 2026. Not documented for Business Discovery on other
# accounts: requested, and dropped for the rest of the run if Meta rejects them.
OPTIONAL_MEDIA_FIELDS = "shares_count,reposts_count"
RATE_LIMIT_CODES = {4, 17, 32, 613} | set(range(80001, 80015))
AUTH_CODES = {102, 190, 463, 467, 10, 200, 3}
UNAVAILABLE_CODES = {110, 24}
UNAVAILABLE_SUBCODES = {2207013, 2207009}


@dataclass
class MetaResult:
    outcome: str
    profile: dict | None = None
    posts: list[dict] = field(default_factory=list)
    pages: int = 0
    exhausted: bool = False      # reached the end of the account's media
    detail: str = ""
    raw_pages: list = field(default_factory=list)


def _fmt(media_type: str | None, product_type: str | None) -> str | None:
    if (product_type or "").upper() == "REELS":
        return "reel"
    return {"CAROUSEL_ALBUM": "carousel", "VIDEO": "video", "IMAGE": "image"}.get((media_type or "").upper())


def normalize_media(m: dict) -> dict | None:
    from ..parse.text import hashtags, mentions
    code = shortcode_from_url(m.get("permalink"))
    if not code:
        return None
    caption = m.get("caption")
    ts = parse_iso(m.get("timestamp"))
    return {
        "shortcode": code,
        "graph_media_id": m.get("id"),
        "taken_at": iso(ts) if ts else None,
        "format": _fmt(m.get("media_type"), m.get("media_product_type")),
        "product_type": (m.get("media_product_type") or "").lower() or None,
        "caption": caption,
        "hashtags": hashtags(caption),
        "mentions": mentions(caption),
        "permalink": m.get("permalink"),
        # like_count is omitted when the owner hides likes: keep it None, never 0.
        "likes": int_or_none(m.get("like_count")),
        "comments": int_or_none(m.get("comments_count")),
        "views": int_or_none(m.get("view_count")),
        "shares": int_or_none(m.get("shares_count")),
        "reposts": int_or_none(m.get("reposts_count")),
        "metrics_hidden": "like_count" not in m,
    }


def normalize_profile(bd: dict) -> dict:
    return {
        "username": (bd.get("username") or "").lower(),
        "graph_id": str(bd["id"]) if bd.get("id") else None,
        "followers": int_or_none(bd.get("followers_count")),
        "following": int_or_none(bd.get("follows_count")),
        "media_count": int_or_none(bd.get("media_count")),
        "full_name": bd.get("name"),
        "biography": bd.get("biography"),
        "website": bd.get("website"),
        "bio_links": None,
        "is_verified": None,
        "is_private": False,
        "profile_pic_url": bd.get("profile_picture_url"),
    }


def classify_error(status: int, body: dict | None) -> tuple[str, str]:
    err = (body or {}).get("error") or {}
    code, sub = err.get("code"), err.get("error_subcode")
    msg = f"HTTP {status} code={code} subcode={sub} {err.get('message', '')}".strip()
    if code in RATE_LIMIT_CODES or status == 429:
        return Outcome.RATE_LIMITED.value, msg
    if code in AUTH_CODES or status in (401, 403) and code is None:
        return Outcome.AUTH_ERROR.value, msg
    if code in UNAVAILABLE_CODES or sub in UNAVAILABLE_SUBCODES or (code == 100 and "business" in msg.lower()):
        return Outcome.UNAVAILABLE.value, msg
    return Outcome.ERROR.value, msg


def usage_pct(headers) -> tuple[float, int | None]:
    """Highest usage percentage from X-Business-Use-Case-Usage / X-App-Usage, and minutes to regain access."""
    best, regain = 0.0, None
    for name in ("x-business-use-case-usage", "x-app-usage", "x-ad-account-usage"):
        raw = headers.get(name) if headers else None
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        entries = []
        if isinstance(data, dict) and any(isinstance(v, list) for v in data.values()):
            for v in data.values():
                entries.extend(x for x in v if isinstance(x, dict))
        elif isinstance(data, dict):
            entries.append(data)
        for e in entries:
            for k in ("call_count", "total_cputime", "total_time"):
                if isinstance(e.get(k), (int, float)):
                    best = max(best, float(e[k]))
            if isinstance(e.get("estimated_time_to_regain_access"), (int, float)) and e["estimated_time_to_regain_access"]:
                regain = int(e["estimated_time_to_regain_access"])
    return best, regain


class MetaBusinessDiscovery:
    def __init__(self, token: str, ig_user_id: str, version: str, pacer: Pacer,
                 recorder: Recorder = null_recorder, session: requests.Session | None = None,
                 page_size: int = 50, max_pages: int = 4, timeout: float = 30):
        self.token = token
        self.ig_user_id = ig_user_id
        self.version = version
        self.pacer = pacer
        self.recorder = recorder
        self.session = session or requests.Session()
        self.page_size = page_size
        self.max_pages = max_pages
        self.timeout = timeout
        self.use_optional_fields = True

    def _fields(self, username: str, after: str | None) -> str:
        cursor = f".after({after})" if after else ""
        media = MEDIA_FIELDS + ("," + OPTIONAL_MEDIA_FIELDS if self.use_optional_fields else "")
        return (f"business_discovery.username({username}){{{PROFILE_FIELDS},"
                f"media.limit({self.page_size}){cursor}{{{media}}}}}")

    def _call(self, username: str, after: str | None) -> tuple[str, dict | None, str]:
        url = f"https://graph.facebook.com/{self.version}/{self.ig_user_id}"
        for attempt in (1, 2, 3):
            params = {"fields": self._fields(username, after), "access_token": self.token}
            self.pacer.before_request()
            t0 = time.monotonic()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                self.recorder("meta", "business_discovery", username, Outcome.ERROR.value, None,
                              int((time.monotonic() - t0) * 1000))
                if attempt < 3:
                    time.sleep(10)
                    continue
                return Outcome.ERROR.value, None, f"network error: {type(exc).__name__}"
            ms = int((time.monotonic() - t0) * 1000)
            try:
                body = resp.json()
            except ValueError:
                body = None
            pct, regain = usage_pct(resp.headers)
            if resp.status_code == 200 and body and "business_discovery" in body:
                self.recorder("meta", "business_discovery", username, Outcome.OK.value, 200, ms)
                self.pacer.ok()
                if pct >= 95:
                    self.pacer.slow_down((regain or 15) * 60, f"Meta usage at {pct:.0f}%")
                elif pct >= 75:
                    self.pacer.slow_down(30, f"Meta usage at {pct:.0f}%")
                return Outcome.OK.value, body["business_discovery"], ""
            outcome, msg = classify_error(resp.status_code, body)
            self.recorder("meta", "business_discovery", username, outcome, resp.status_code, ms)
            err = (body or {}).get("error") or {}
            if self.use_optional_fields and err.get("code") == 100 and any(
                    f in (err.get("message") or "") for f in OPTIONAL_MEDIA_FIELDS.split(",")):
                self.use_optional_fields = False   # shares/reposts not served here: retry without them
                continue
            if outcome == Outcome.RATE_LIMITED.value:
                if self.pacer.rate_limited(msg) and attempt < 3:
                    continue
                return outcome, None, msg
            if outcome == Outcome.AUTH_ERROR.value:
                self.pacer.stop("Meta token/permission problem: " + msg)
                return outcome, None, msg
            if outcome == Outcome.ERROR.value and resp.status_code >= 500 and attempt < 3:
                time.sleep(30)
                continue
            return outcome, None, msg
        return Outcome.ERROR.value, None, "retry exhausted"

    def fetch(self, username: str, now: datetime, n: int = 30, days: int = 30) -> MetaResult:
        result = MetaResult(outcome=Outcome.OK.value)
        after = None
        seen = set()
        while result.pages < self.max_pages:
            outcome, bd, msg = self._call(username, after)
            if outcome != Outcome.OK.value:
                if result.pages == 0:
                    return MetaResult(outcome=outcome, detail=msg)
                result.outcome, result.detail = Outcome.OK.value, f"partial: {msg}"
                break
            result.pages += 1
            result.raw_pages.append(bd)
            if result.profile is None:
                result.profile = normalize_profile(bd)
            media = bd.get("media") or {}
            batch = media.get("data") or []
            for m in batch:
                p = normalize_media(m)
                if p and p["shortcode"] not in seen:
                    seen.add(p["shortcode"])
                    result.posts.append(p)
            after = ((media.get("paging") or {}).get("cursors") or {}).get("after")
            if not batch or len(batch) < self.page_size or not after:
                result.exhausted = True
                break
            if window_satisfied([p["taken_at"] for p in result.posts], now, n, days):
                break
        return result

"""YouTube Data API v3 (official, free quota of 10,000 units per day per Google Cloud project).

Per channel, about 3 units:
  channels.list      1 unit   subscribers, total views, video count, description, keywords, topics, uploads playlist
  playlistItems.list 1 unit   latest 50 uploads (one more page when the 30-day window needs it)
  videos.list        1 unit   per 50 videos: views, likes, comments, duration, tags, category, language,
                              made-for-kids, "includes paid promotion" flag, topics
Legacy /c/<name> links cannot be looked up directly; resolving them costs a 100-unit search and is
off unless --allow-search is given.

Not available without the creator's own OAuth consent (YouTube Analytics API): shares, watch time,
retention, audience demographics, revenue.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from ..budget import Outcome, Pacer
from ..util import int_or_none, iso, parse_iso

API = "https://www.googleapis.com/youtube/v3"
UNITS = {"channels": 1, "playlistItems": 1, "videos": 1, "search": 100}
QUOTA_TZ = ZoneInfo("America/Los_Angeles")  # YouTube quota resets at midnight Pacific time
SHORT_MAX_S = 180


def iso_duration_s(value: str | None) -> int | None:
    if not value:
        return None
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value)
    if not m:
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


@dataclass
class ChannelResult:
    outcome: str
    channel: dict | None = None
    videos: list[dict] = field(default_factory=list)
    units: int = 0
    detail: str = ""
    reached_end: bool = False


class QuotaExhausted(Exception):
    pass


class YouTubeClient:
    def __init__(self, api_key: str, pacer: Pacer, daily_units: int = 9000, units_used_today=lambda: 0,
                 recorder=lambda *a: None, session: requests.Session | None = None, allow_search: bool = False):
        self.key = api_key
        self.pacer = pacer
        self.daily_units = daily_units
        self.units_used_today = units_used_today
        self.recorder = recorder
        self.session = session or requests.Session()
        self.allow_search = allow_search
        self.units_this_run = 0

    def _get(self, endpoint: str, channel: str, **params) -> tuple[str, dict | None, str]:
        cost = UNITS[endpoint]
        if self.units_used_today() + cost > self.daily_units:
            raise QuotaExhausted(f"local daily cap of {self.daily_units} units reached (resets midnight Pacific)")
        for attempt in (1, 2):
            self.pacer.before_request()
            try:
                resp = self.session.get(f"{API}/{endpoint}", params={**params, "key": self.key}, timeout=30)
            except requests.RequestException as exc:
                self.recorder(channel, endpoint, cost, Outcome.ERROR.value, None)
                if attempt == 1:
                    time.sleep(5)
                    continue
                return Outcome.ERROR.value, None, type(exc).__name__
            self.units_this_run += cost
            try:
                body = resp.json()
            except ValueError:
                body = None
            if resp.status_code == 200 and body is not None:
                self.recorder(channel, endpoint, cost, Outcome.OK.value, 200)
                return Outcome.OK.value, body, ""
            reasons = [e.get("reason") for e in ((body or {}).get("error") or {}).get("errors", [])]
            msg = f"HTTP {resp.status_code} {reasons}"
            if any(r in ("quotaExceeded", "dailyLimitExceeded") for r in reasons):
                self.recorder(channel, endpoint, cost, Outcome.RATE_LIMITED.value, resp.status_code)
                self.pacer.stop("YouTube daily quota exhausted")
                raise QuotaExhausted(msg)
            if any(r in ("rateLimitExceeded", "userRateLimitExceeded") for r in reasons):
                self.recorder(channel, endpoint, cost, Outcome.RATE_LIMITED.value, resp.status_code)
                if self.pacer.rate_limited(msg) and attempt == 1:
                    continue
                return Outcome.RATE_LIMITED.value, None, msg
            if resp.status_code in (400, 403) and any(r in ("keyInvalid", "forbidden", "accessNotConfigured")
                                                      for r in reasons):
                self.recorder(channel, endpoint, cost, Outcome.AUTH_ERROR.value, resp.status_code)
                self.pacer.stop("YouTube API key problem: " + msg)
                return Outcome.AUTH_ERROR.value, None, msg
            outcome = Outcome.NOT_FOUND.value if resp.status_code == 404 else Outcome.ERROR.value
            self.recorder(channel, endpoint, cost, outcome, resp.status_code)
            if resp.status_code >= 500 and attempt == 1:
                time.sleep(10)
                continue
            return outcome, None, msg
        return Outcome.ERROR.value, None, "retry exhausted"

    # ------------------------------------------------------------ steps

    def channel(self, ref: str) -> tuple[str, dict | None, str]:
        part = "snippet,statistics,contentDetails,brandingSettings,topicDetails"
        kind, _, value = ref.partition(":") if not ref.startswith("@") else ("handle", "", ref)
        if kind == "handle":
            outcome, body, msg = self._get("channels", ref, part=part, forHandle=value)
        elif kind == "channel":
            outcome, body, msg = self._get("channels", ref, part=part, id=value)
        elif kind == "user":
            outcome, body, msg = self._get("channels", ref, part=part, forUsername=value)
        elif kind == "c":
            if not self.allow_search:
                return Outcome.NOT_FOUND.value, None, "legacy /c/ link: needs --allow-search (100 units) or an @handle"
            outcome, body, msg = self._get("search", ref, part="snippet", type="channel", q=value, maxResults=1)
            if outcome != Outcome.OK.value or not (body or {}).get("items"):
                return outcome if outcome != Outcome.OK.value else Outcome.NOT_FOUND.value, None, msg or "no search match"
            cid = body["items"][0]["snippet"]["channelId"]
            outcome, body, msg = self._get("channels", ref, part=part, id=cid)
        else:
            return Outcome.ERROR.value, None, f"unrecognised YouTube reference {ref}"
        if outcome != Outcome.OK.value:
            return outcome, None, msg
        items = (body or {}).get("items") or []
        if not items:
            return Outcome.NOT_FOUND.value, None, "no channel for this reference"
        return Outcome.OK.value, normalize_channel(items[0]), ""

    def fetch(self, ref: str, now: datetime, n: int = 30, days: int = 30, max_pages: int = 4) -> ChannelResult:
        start = self.units_this_run
        outcome, ch, msg = self.channel(ref)
        if outcome != Outcome.OK.value:
            return ChannelResult(outcome, detail=msg, units=self.units_this_run - start)
        res = ChannelResult(Outcome.OK.value, channel=ch)
        ids: list[str] = []
        token, pages = None, 0
        cutoff = now - timedelta(days=days)
        uploads = ch.get("uploads_playlist")
        while uploads and pages < max_pages:
            params = {"part": "contentDetails", "playlistId": uploads, "maxResults": 50}
            if token:
                params["pageToken"] = token
            outcome, body, msg = self._get("playlistItems", ref, **params)
            if outcome != Outcome.OK.value:
                if pages == 0:
                    res.detail = f"uploads not readable: {msg}"
                break
            pages += 1
            items = body.get("items") or []
            dates = []
            for it in items:
                cd = it.get("contentDetails") or {}
                if cd.get("videoId"):
                    ids.append(cd["videoId"])
                    dates.append(parse_iso(cd.get("videoPublishedAt")))
            token = body.get("nextPageToken")
            oldest = min((d for d in dates if d), default=None)
            if not token:
                res.reached_end = True
                break
            if len(ids) >= n and oldest and oldest < cutoff:
                break
        for i in range(0, len(ids), 50):
            chunk = ids[i:i + 50]
            outcome, body, msg = self._get("videos", ref, id=",".join(chunk),
                                           part="snippet,statistics,contentDetails,status,"
                                                "paidProductPlacementDetails,topicDetails,liveStreamingDetails")
            if outcome != Outcome.OK.value:
                res.detail = f"video details incomplete: {msg}"
                break
            res.videos.extend(normalize_video(v) for v in body.get("items") or [])
        res.units = self.units_this_run - start
        return res


def normalize_channel(item: dict) -> dict:
    sn, st = item.get("snippet") or {}, item.get("statistics") or {}
    hidden = bool(st.get("hiddenSubscriberCount"))
    return {
        "channel_id": item.get("id"),
        "handle": (sn.get("customUrl") or "").lower() or None,
        "title": sn.get("title"),
        "description": sn.get("description"),
        "country": sn.get("country"),
        "published_at": iso(parse_iso(sn.get("publishedAt"))) if sn.get("publishedAt") else None,
        "subscribers": None if hidden else _int(st.get("subscriberCount")),
        "total_views": _int(st.get("viewCount")),
        "video_count": _int(st.get("videoCount")),
        "keywords": ((item.get("brandingSettings") or {}).get("channel") or {}).get("keywords"),
        "topics": [t.rsplit("/", 1)[-1].replace("_", " ") for t in
                   (item.get("topicDetails") or {}).get("topicCategories") or []],
        "uploads_playlist": ((item.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads"),
    }


def normalize_video(v: dict) -> dict:
    sn, st, cd = v.get("snippet") or {}, v.get("statistics") or {}, v.get("contentDetails") or {}
    dur = iso_duration_s(cd.get("duration"))
    live = bool(v.get("liveStreamingDetails")) or sn.get("liveBroadcastContent") in ("live", "upcoming")
    return {
        "video_id": v.get("id"),
        "published_at": iso(parse_iso(sn.get("publishedAt"))) if sn.get("publishedAt") else None,
        "title": sn.get("title"),
        "description": sn.get("description"),
        "duration_s": dur,
        "format": "live" if live else ("short_guess" if dur is not None and dur <= SHORT_MAX_S else "video"),
        "category_id": sn.get("categoryId"),
        "tags": sn.get("tags") or [],
        "default_language": sn.get("defaultAudioLanguage") or sn.get("defaultLanguage"),
        "made_for_kids": (v.get("status") or {}).get("madeForKids"),
        "paid_placement": (v.get("paidProductPlacementDetails") or {}).get("hasPaidProductPlacement"),
        "topics": [t.rsplit("/", 1)[-1].replace("_", " ") for t in
                   (v.get("topicDetails") or {}).get("topicCategories") or []],
        "views": _int(st.get("viewCount")),
        "likes": _int(st.get("likeCount")),          # absent when the creator hides likes
        "comments": _int(st.get("commentCount")),    # absent when comments are off
    }


def _int(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return int_or_none(v)


def quota_day(now: datetime) -> str:
    return now.astimezone(QUOTA_TZ).date().isoformat()

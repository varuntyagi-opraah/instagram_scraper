"""Derived analytics computed from stored observations (no extra requests)."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from statistics import mean, median, pstdev

from .metrics import engagement_rate
from .util import parse_iso

IST = timezone(timedelta(hours=5, minutes=30))
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
SCRIPTS = [
    ("hindi", re.compile(r"[ऀ-ॿ]")), ("bengali", re.compile(r"[ঀ-৿]")),
    ("punjabi", re.compile(r"[਀-੿]")), ("gujarati", re.compile(r"[઀-૿]")),
    ("tamil", re.compile(r"[஀-௿]")), ("telugu", re.compile(r"[ఀ-౿]")),
    ("kannada", re.compile(r"[ಀ-೿]")), ("malayalam", re.compile(r"[ഀ-ൿ]")),
    ("urdu_arabic", re.compile(r"[؀-ۿ]")),
]
HINGLISH = {"hai", "hain", "ka", "ki", "ke", "ko", "nahi", "nhi", "kya", "mera", "meri", "mere", "bhai", "yaar",
            "toh", "aur", "kar", "karo", "bhi", "ho", "se", "tum", "tera", "teri", "apna", "apni", "kuch", "sab",
            "abhi", "kaise", "kyun", "matlab", "bas", "wala", "wali", "hum", "main", "mai", "accha", "acha"}
_WORD = re.compile(r"[a-z]+")
BRAND_KINDS = {"collaboration_disclosed", "coauthor", "collab_post_owned_by_brand", "product_integration"}


def caption_language(text: str | None) -> str | None:
    if not text:
        return None
    body = re.sub(r"[#@][^\s]+", " ", text)          # hashtags/mentions say little about language
    for name, rx in SCRIPTS:
        if len(rx.findall(body)) >= 3:
            return name
    words = _WORD.findall(body.lower())
    if len(words) < 2:
        return None
    hits = sum(1 for w in words if w in HINGLISH)
    return "hinglish" if hits >= 2 or (hits and len(words) <= 6) else "english"


def _cv(values: list[int]) -> float | None:
    if len(values) < 3 or not mean(values):
        return None
    return round(pstdev(values) / mean(values), 2)


def consistency_label(cv: float | None) -> str | None:
    if cv is None:
        return None
    return "steady" if cv < 0.6 else "mixed" if cv < 1.2 else "spiky (driven by a few viral posts)"


def instagram_derived(posts: list[dict], followers: int | None, branded: set[str]) -> dict:
    """posts: window posts with relation, taken_at, format, likes, comments, views, plays, caption."""
    own = [p for p in posts if p.get("relation") == "owner"]
    counted = [p for p in own if p.get("likes") is not None and p.get("comments") is not None]

    def med_er(rows):
        ers = [engagement_rate(p["likes"], p["comments"], followers) for p in rows]
        ers = [e for e in ers if e is not None]
        return (round(median(ers), 3) if ers else None), len(ers)

    b_er, b_n = med_er([p for p in counted if p["shortcode"] in branded])
    o_er, o_n = med_er([p for p in counted if p["shortcode"] not in branded])
    owner_er, owner_n = med_er(counted)
    likes = [p["likes"] for p in counted]
    cv = _cv(likes)
    reels = [p for p in own if p.get("format") == "reel"]
    views = [p["views"] for p in reels if p.get("views") is not None]
    plays = [p["plays"] for p in reels if p.get("plays") is not None]
    shares = [p["shares"] for p in own if p.get("shares") is not None]
    exact = [parse_iso(p["taken_at"]).astimezone(IST) for p in own if p.get("taken_at")]
    days = Counter(WEEKDAYS[d.weekday()] for d in exact)
    hours = Counter(d.hour for d in exact)
    langs = Counter(l for l in (caption_language(p.get("caption")) for p in posts) if l)
    return {
        "median_er_pct_own_posts": owner_er, "er_own_posts_coverage": f"{owner_n} of {len(own)} own posts",
        "branded_posts": len([p for p in posts if p["shortcode"] in branded]),
        "branded_median_er_pct": b_er, "organic_median_er_pct": o_er,
        "branded_vs_organic_er_ratio": round(b_er / o_er, 2) if b_er and o_er else None,
        "branded_er_coverage": f"{b_n} branded / {o_n} organic own posts with counts",
        "likes_cv": cv, "consistency": consistency_label(cv),
        "median_reel_views_per_follower": round(median(views) / followers, 3) if views and followers else None,
        "median_reel_plays_per_follower": round(median(plays) / followers, 3) if plays and followers else None,
        "median_shares": round(median(shares)) if shares else None,
        "shares_coverage": f"{len(shares)} of {len(own)} own posts",
        "top_posting_days_ist": ", ".join(d for d, _ in days.most_common(2)) or None,
        "top_posting_hours_ist": ", ".join(f"{h:02d}:00" for h, _ in hours.most_common(2)) or None,
        "caption_languages": ", ".join(f"{l} ({c})" for l, c in langs.most_common(3)) or None,
        "collab_posts_in_window": len([p for p in posts if p.get("relation") != "owner"]),
    }


def growth(snapshots: list[tuple[str, int | None]], min_days: int = 7) -> dict:
    """snapshots: (observed_at, count) oldest→newest. Change from the earliest snapshot ≥ min_days old."""
    pts = [(parse_iso(t), v) for t, v in snapshots if v is not None]
    if len(pts) < 2:
        return {"growth_since": None, "growth_abs": None, "growth_pct": None, "growth_pct_per_30d": None}
    last_t, last_v = pts[-1]
    base = next(((t, v) for t, v in pts if (last_t - t).days >= min_days), None)
    if not base or not base[1]:
        return {"growth_since": None, "growth_abs": None, "growth_pct": None, "growth_pct_per_30d": None}
    t0, v0 = base
    days = max((last_t - t0).days, 1)
    pct = (last_v - v0) / v0 * 100
    return {"growth_since": t0.date().isoformat(), "growth_abs": last_v - v0, "growth_pct": round(pct, 2),
            "growth_pct_per_30d": round(pct * 30 / days, 2)}


def youtube_stats(videos: list[dict], now: datetime) -> dict:
    """videos: window videos with format, views, likes, comments, published_at, paid_placement."""
    def med(xs):
        xs = [x for x in xs if x is not None]
        return round(median(xs)) if xs else None
    longs = [v for v in videos if v.get("format") == "video"]
    shorts = [v for v in videos if v.get("format") == "short_guess"]
    eng = [round((v["likes"] + v["comments"]) / v["views"] * 100, 3) for v in videos
           if v.get("views") and v.get("likes") is not None and v.get("comments") is not None]
    dates = sorted(d for d in (parse_iso(v.get("published_at")) for v in videos) if d)
    span = (dates[-1] - dates[0]).days if len(dates) > 1 else None
    return {
        "videos_in_sample": len(videos), "long_videos": len(longs), "shorts_guess": len(shorts),
        "live": len([v for v in videos if v.get("format") == "live"]),
        "videos_last_30_days": len([d for d in dates if d >= now - timedelta(days=30)]),
        "uploads_per_week": round(len(dates) / (span / 7), 2) if span else None,
        "median_views_long": med([v.get("views") for v in longs]),
        "median_views_shorts": med([v.get("views") for v in shorts]),
        "median_views_all": med([v.get("views") for v in videos]),
        "median_engagement_pct_of_views": round(median(eng), 3) if eng else None,
        "engagement_coverage": f"{len(eng)} of {len(videos)} videos",
        "paid_promotion_videos": len([v for v in videos if v.get("paid_placement")]),
        "last_upload_at": dates[-1].isoformat() if dates else None,
    }

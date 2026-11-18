"""Window selection and engagement summaries. Missing values stay missing."""

from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median

from .util import parse_iso


def select_window(posts: list[dict], now: datetime, n: int = 30, days: int = 30) -> list[str]:
    """Shortcodes in (latest n by taken_at) ∪ (taken_at within `days`), newest first.

    Pinned posts get no special treatment: ordering is by publication time, so an old pin
    cannot displace a recent post, and a recent pin is still eligible.
    """
    dated = [(parse_iso(p.get("taken_at")), p["shortcode"]) for p in posts if p.get("taken_at")]
    dated.sort(key=lambda t: t[0], reverse=True)
    cutoff = now - timedelta(days=days)
    keep = [code for _, code in dated[:n]]
    keep += [code for t, code in dated[n:] if t >= cutoff]
    return list(dict.fromkeys(keep))


def window_satisfied(ordered_dates: list[str | None], now: datetime, n: int, days: int, pin_slack: int = 3) -> bool:
    """Have we paginated far enough? `ordered_dates` are in feed order (pins first, then newest→oldest).

    True once we hold n + pin_slack items and the last (oldest-positioned) one predates the cutoff.
    """
    if len(ordered_dates) < n + pin_slack:
        return False
    last = parse_iso(ordered_dates[-1]) if ordered_dates[-1] else None
    if last is None:
        return False
    return last < now - timedelta(days=days)


def engagement_rate(likes, comments, followers) -> float | None:
    if likes is None or comments is None or not followers:
        return None
    return round((likes + comments) / followers * 100, 3)


def _avg(values):
    return round(sum(values) / len(values)) if values else None


def summarize(posts: list[dict], followers: int | None, now: datetime) -> dict:
    """posts: dicts with taken_at, format, likes, comments, views, plays (None when unknown)."""
    n = len(posts)
    with_counts = [p for p in posts if p.get("likes") is not None and p.get("comments") is not None]
    ers = [engagement_rate(p["likes"], p["comments"], followers) for p in with_counts] if followers else []
    ers = [e for e in ers if e is not None]
    reels = [p for p in posts if p.get("format") == "reel"]
    views = [p["views"] for p in reels if p.get("views") is not None]
    plays = [p["plays"] for p in reels if p.get("plays") is not None]
    # Exact time when known, else the day-precision date from Instagram's image description.
    dates = sorted(d for d in (parse_iso(p.get("taken_at") or p.get("date") or p.get("approx_date")) for p in posts) if d)
    span_days = (dates[-1] - dates[0]).days if len(dates) > 1 else None
    last30 = [d for d in dates if d >= now - timedelta(days=30)]
    formats: dict[str, int] = {}
    for p in posts:
        formats[p.get("format") or "unknown"] = formats.get(p.get("format") or "unknown", 0) + 1
    return {
        "posts_in_sample": n,
        "formats": formats,
        "first_post_at": dates[0].isoformat() if dates else None,
        "last_post_at": dates[-1].isoformat() if dates else None,
        "posts_last_30_days": len(last30),
        "posts_per_week": round(len(dates) / (span_days / 7), 2) if span_days else None,
        "total_likes": sum(p["likes"] for p in with_counts) if with_counts else None,
        "total_comments": sum(p["comments"] for p in with_counts) if with_counts else None,
        "median_likes": round(median(p["likes"] for p in with_counts)) if with_counts else None,
        "median_comments": round(median(p["comments"] for p in with_counts)) if with_counts else None,
        "median_er_pct": round(median(ers), 3) if ers else None,
        "er_coverage": f"{len(ers)} of {n} posts",
        "avg_reel_views": _avg(views),
        "median_reel_views": round(median(views)) if views else None,
        "views_coverage": f"{len(views)} of {len(reels)} Reels",
        "avg_reel_plays": _avg(plays),
        "plays_coverage": f"{len(plays)} of {len(reels)} Reels",
    }

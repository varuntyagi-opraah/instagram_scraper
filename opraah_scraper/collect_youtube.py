"""Store YouTube channels and videos for roster creators (vendor code ↔ YouTube link from the roster)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from . import db
from .brands import Registry, derive_edges
from .budget import Outcome, SourceStopped
from .parse.text import hashtags, mentions
from .sources.youtube import QuotaExhausted, YouTubeClient, quota_day
from .util import dumps, iso, parse_iso


@dataclass
class YTReport:
    ref: str
    status: str
    channel_id: str | None = None
    videos: int = 0
    units: int = 0
    detail: str = ""


def units_used_today(conn, now: datetime) -> int:
    day = quota_day(now)
    total = 0
    for r in conn.execute("SELECT ts, units FROM yt_request_log WHERE ts >= ?",
                          (iso(now - timedelta(days=2)),)):
        if quota_day(parse_iso(r["ts"])) == day:
            total += r["units"]
    return total


def store_channel(conn, ch: dict, run_id: int | None, ts: str) -> None:
    conn.execute("""INSERT INTO yt_channel (channel_id, handle, title, country, published_at, uploads_playlist,
                        first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(channel_id) DO UPDATE SET handle=excluded.handle, title=excluded.title,
                        country=COALESCE(excluded.country, yt_channel.country),
                        uploads_playlist=excluded.uploads_playlist, last_seen=excluded.last_seen""",
                 (ch["channel_id"], ch["handle"], ch["title"], ch["country"], ch["published_at"],
                  ch["uploads_playlist"], ts, ts))
    conn.execute("""INSERT OR REPLACE INTO yt_channel_snapshot (channel_id, observed_at, run_id, subscribers,
                        total_views, video_count, description, keywords, topics_json) VALUES (?,?,?,?,?,?,?,?,?)""",
                 (ch["channel_id"], ts, run_id, ch["subscribers"], ch["total_views"], ch["video_count"],
                  ch["description"], ch["keywords"], dumps(ch["topics"])))


def store_video(conn, channel_id: str, v: dict, run_id: int | None, ts: str) -> None:
    conn.execute("""INSERT INTO yt_video (video_id, channel_id, published_at, title, description, duration_s, format,
                        category_id, tags_json, default_language, made_for_kids, paid_placement, topics_json,
                        first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(video_id) DO UPDATE SET title=excluded.title, description=excluded.description,
                        duration_s=excluded.duration_s, format=excluded.format, tags_json=excluded.tags_json,
                        paid_placement=excluded.paid_placement, topics_json=excluded.topics_json,
                        last_seen=excluded.last_seen""",
                 (v["video_id"], channel_id, v["published_at"], v["title"], v["description"], v["duration_s"],
                  v["format"], v["category_id"], dumps(v["tags"]), v["default_language"],
                  None if v["made_for_kids"] is None else int(v["made_for_kids"]),
                  None if v["paid_placement"] is None else int(v["paid_placement"]), dumps(v["topics"]), ts, ts))
    if any(v[k] is not None for k in ("views", "likes", "comments")):
        conn.execute("""INSERT OR REPLACE INTO yt_video_metric (video_id, observed_at, run_id, views, likes, comments)
                        VALUES (?,?,?,?,?,?)""", (v["video_id"], ts, run_id, v["views"], v["likes"], v["comments"]))


def video_edges(v: dict, registry: Registry, roster_handles: set[str]):
    """Brand evidence from the title/description/tags, plus YouTube's paid-promotion flag."""
    text = f"{v.get('title') or ''}\n{v.get('description') or ''}"
    post = {"caption": text, "hashtags": hashtags(text) + [t.lower().replace(" ", "") for t in v.get("tags") or []],
            "mentions": mentions(text), "coauthors": [], "usertags": [], "owner_username": None,
            "is_paid_partnership": bool(v.get("paid_placement"))}
    return derive_edges(post, "", registry, roster_handles)


def store_edges(conn, channel_id: str, v: dict, registry: Registry, roster_handles: set[str], ts: str) -> None:
    own = {(r[0] or "").lstrip("@").lower() for r in conn.execute(
        "SELECT handle FROM yt_channel WHERE channel_id=?", (channel_id,))}
    own |= {r[0] for r in conn.execute(
        """SELECT rl.username FROM roster_youtube ry JOIN roster_link rl USING (vendor_code)
           WHERE ry.channel_id=?""", (channel_id,))}
    for e in video_edges(v, registry, roster_handles):
        if e.brand_handle.lstrip("@").lower() in own:
            continue   # the creator's own channel/Instagram links in the description are not collaborations
        rule = "youtube_paid_promotion_flag" if e.rule_id == "paid_partnership_label_unnamed" else e.rule_id
        text = "YouTube 'includes paid promotion' flag" if rule == "youtube_paid_promotion_flag" else e.evidence_text
        conn.execute("""INSERT INTO yt_brand_evidence (video_id, channel_id, brand_handle, kind, evidence_text, rule_id,
                            review_status, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(video_id, brand_handle, kind) DO UPDATE SET evidence_text=excluded.evidence_text,
                            last_seen=excluded.last_seen,
                            review_status=CASE WHEN yt_brand_evidence.review_status IN ('confirmed','rejected')
                                THEN yt_brand_evidence.review_status ELSE excluded.review_status END""",
                     (v["video_id"], channel_id, e.brand_handle, e.kind, text[:500], rule, e.review_status, ts, ts))


def collect_refs(conn, client: YouTubeClient, refs: list[str], run_id: int | None, now_fn, *,
                 n: int = 30, days: int = 30, due_days: int | None = None, log=print) -> list[YTReport]:
    registry = Registry.load()
    roster_handles = {r[0] for r in conn.execute("SELECT DISTINCT username FROM roster_link")}
    reports = []
    for i, ref in enumerate(refs, 1):
        now = now_fn()
        ts = iso(now)
        known = conn.execute("SELECT channel_id FROM roster_youtube WHERE yt_ref=? AND channel_id IS NOT NULL",
                             (ref,)).fetchone()
        if due_days is not None and known:
            row = conn.execute("SELECT last_full_at FROM yt_channel WHERE channel_id=?", (known[0],)).fetchone()
            if row and row[0] and parse_iso(row[0]) > now - timedelta(days=due_days):
                reports.append(YTReport(ref, Outcome.SKIPPED.value, known[0]))
                continue
        try:
            res = client.fetch(f"channel:{known[0]}" if known else ref, now, n, days)
        except (QuotaExhausted, SourceStopped) as exc:
            log(f"YouTube stopped: {exc}. Run the same command tomorrow to continue.")
            reports.append(YTReport(ref, Outcome.RATE_LIMITED.value, detail=str(exc)))
            break
        rep = YTReport(ref, res.outcome, units=res.units, detail=res.detail)
        if res.outcome == Outcome.OK.value:
            ch = res.channel
            rep.channel_id = ch["channel_id"]
            store_channel(conn, ch, run_id, ts)
            conn.execute("UPDATE roster_youtube SET channel_id=? WHERE yt_ref=?", (ch["channel_id"], ref))
            for v in res.videos:
                store_video(conn, ch["channel_id"], v, run_id, ts)
                store_edges(conn, ch["channel_id"], v, registry, roster_handles, ts)
            rep.videos = len(res.videos)
            if res.detail:
                rep.status = Outcome.PARTIAL.value
            conn.execute("UPDATE yt_channel SET last_full_at=?, last_status=? WHERE channel_id=?",
                         (ts, rep.status, ch["channel_id"]))
        conn.commit()
        log(f"[{i}/{len(refs)}] YouTube {ref} -> {rep.status}: {rep.videos} videos, {rep.units} units"
            + (f"  {rep.detail}" if rep.detail else ""))
        reports.append(rep)
    return reports

"""One complete, structured record per creator (vendor code), across Instagram and YouTube.

The CSV exports, the per-creator folders and their Markdown pages are all rendered from these
records, so every output says the same thing. Roster rows without a vendor code (plain handle
lists) become records keyed by handle.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime

from . import kb
from .brands import BRAND_WORK_KINDS
from .derive import growth, instagram_derived, youtube_stats
from .metrics import select_window, summarize
from .topics import creator_themes, load_taxonomy
from .util import loads, parse_iso


def _brand_lists(evid: list[dict], date_key: str = "taken_at") -> dict:
    """Group evidence rows into brands worked with / other collaborators / mentions / creator collabs."""
    disclosed = {e["brand_handle"] for e in evid if e["kind"] in ("collaboration_disclosed", "product_integration")}
    groups: dict[str, dict] = defaultdict(lambda: {"posts": set(), "kinds": set(), "last": None})
    for e in evid:
        if e["brand_handle"].startswith("("):
            continue
        g = groups[(e["brand_handle"], _bucket(e, disclosed))]
        g["posts"].add(e["shortcode"])
        g["kinds"].add(e["kind"])
        d = e.get(date_key) or e.get("approx_date")
        if d and (g["last"] is None or d > g["last"]):
            g["last"] = d
        g["name"], g["category"] = e.get("brand_name"), e.get("brand_category")
    out = {"brands_worked_with": [], "other_collaborators": [], "brand_mentions": [], "creator_collabs": []}
    for (handle, bucket), g in groups.items():
        if bucket is None:
            continue
        out[bucket].append({"handle": handle, "name": g["name"], "category": g["category"], "posts": len(g["posts"]),
                            "kinds": sorted(g["kinds"]), "last_post_date": (g["last"] or "")[:10] or None})
    for k in out:
        out[k].sort(key=lambda b: (-b["posts"], b["handle"]))
    return out


def _bucket(e: dict, disclosed: set[str]) -> str | None:
    if e["kind"] == "creator_collab":
        return "creator_collabs"
    if e["kind"] in BRAND_WORK_KINDS and e.get("brand_kind") != "creator":
        return "brands_worked_with" if e.get("brand_kind") == "brand" or e["brand_handle"] in disclosed \
            else "other_collaborators"
    if e["kind"] in ("mention", "hashtag", "event_association") and e.get("brand_kind") == "brand":
        return "brand_mentions"
    return None


def instagram_block(conn, account_id: int, handle: str, now: datetime, n: int, days: int, taxonomy) -> dict:
    prof = kb.latest_profile(conn, account_id)
    posts = kb.window_posts(conn, account_id, now, n, days, taxonomy)
    evid = kb.evidence(conn, account_id)
    branded = {e["shortcode"] for e in evid if e["kind"] in BRAND_WORK_KINDS}
    for p in posts:
        p["is_branded"] = p["shortcode"] in branded
    stats = summarize(posts, prof.get("followers"), now)
    snaps = [(r["observed_at"], r["followers"]) for r in conn.execute(
        "SELECT observed_at, followers FROM profile_snapshot WHERE account_id=? ORDER BY observed_at", (account_id,))]
    hrow = conn.execute("SELECT last_status, meta_available, metrics_level, last_full_at, last_light_at "
                        "FROM ig_handle WHERE username=?", (handle,)).fetchone()
    st = conn.execute("""SELECT status, coverage_json, finished_at FROM run_account WHERE username=?
                         AND status <> 'skipped_not_due' ORDER BY run_id DESC LIMIT 1""", (handle,)).fetchone()
    return {
        "handle": handle, "url": f"https://www.instagram.com/{handle}/",
        "status": st["status"] if st else None, "last_collected_at": st["finished_at"] if st else None,
        "coverage": loads(st["coverage_json"], {}) if st else {},
        "metrics_level": hrow["metrics_level"] if hrow else None,
        "meta_available": hrow["meta_available"] if hrow else None,
        "profile": {k: prof.get(k) for k in ("followers", "followers_source", "followers_observed_at", "following",
                                              "media_count", "full_name", "biography", "website", "is_verified")}
                   | {"bio_links": [b.get("url") for b in prof.get("bio_links") or []]},
        "stats": stats,
        "derived": instagram_derived(posts, prof.get("followers"), branded),
        "growth": growth(snaps),
        "themes": [{"theme": t, "posts": c} for t, c in creator_themes(posts, taxonomy)],
        "content_locations": [{"location": l, "posts": c} for l, c in
                              Counter(p["location_name"] for p in posts if p.get("location_name")).most_common(5)],
        **_brand_lists(evid),
        "_posts": posts, "_evidence": evid, "_snapshots": snaps,
    }


def youtube_block(conn, channel_id: str, now: datetime, n: int, days: int) -> dict:
    ch = conn.execute("SELECT * FROM yt_channel WHERE channel_id=?", (channel_id,)).fetchone()
    snap = conn.execute("""SELECT * FROM yt_channel_snapshot WHERE channel_id=? ORDER BY observed_at DESC, id DESC
                           LIMIT 1""", (channel_id,)).fetchone()
    vids = [dict(r) for r in conn.execute("SELECT * FROM yt_video WHERE channel_id=?", (channel_id,))]
    keep = select_window([{"shortcode": v["video_id"], "taken_at": v["published_at"]} for v in vids], now, n, days)
    by = {v["video_id"]: v for v in vids}
    window = []
    for vid in keep:
        v = by[vid]
        m = conn.execute("""SELECT views, likes, comments, observed_at FROM yt_video_metric WHERE video_id=?
                            ORDER BY observed_at DESC, id DESC LIMIT 1""", (vid,)).fetchone()
        v.update(dict(m) if m else {"views": None, "likes": None, "comments": None, "observed_at": None})
        v["tags"] = loads(v.pop("tags_json"), [])
        v["topics"] = loads(v.pop("topics_json"), [])
        v["url"] = (f"https://www.youtube.com/shorts/{vid}" if v["format"] == "short_guess"
                    else f"https://www.youtube.com/watch?v={vid}")
        window.append(v)
    evid = [dict(r) | {"shortcode": r["video_id"]} for r in conn.execute(
        """SELECT e.*, b.name AS brand_name, b.category AS brand_category, b.kind AS brand_kind, v.published_at
           FROM yt_brand_evidence e JOIN yt_video v USING (video_id) LEFT JOIN brand b ON b.handle = e.brand_handle
           WHERE e.channel_id=? ORDER BY v.published_at DESC""", (channel_id,))]
    snaps = [(r["observed_at"], r["subscribers"]) for r in conn.execute(
        "SELECT observed_at, subscribers FROM yt_channel_snapshot WHERE channel_id=? ORDER BY observed_at", (channel_id,))]
    return {
        "channel_id": channel_id, "handle": ch["handle"] if ch else None, "title": ch["title"] if ch else None,
        "url": f"https://www.youtube.com/{ch['handle']}" if ch and ch["handle"] else
               f"https://www.youtube.com/channel/{channel_id}",
        "country": ch["country"] if ch else None, "status": ch["last_status"] if ch else None,
        "last_collected_at": ch["last_full_at"] if ch else None,
        "subscribers": snap["subscribers"] if snap else None, "total_views": snap["total_views"] if snap else None,
        "video_count": snap["video_count"] if snap else None, "observed_at": snap["observed_at"] if snap else None,
        "channel_topics": loads(snap["topics_json"], []) if snap else [],
        "stats": youtube_stats(window, now),
        "growth": growth(snaps),
        **_brand_lists(evid, "published_at"),
        "_videos": window, "_evidence": evid,
    }


def build_profiles(conn, now: datetime, n: int = 30, days: int = 30) -> list[dict]:
    taxonomy = load_taxonomy()
    ig_cache: dict[int, dict] = {}
    yt_cache: dict[str, dict] = {}
    handle_to_account = {r["username"]: r["account_id"] for r in
                         conn.execute("SELECT username, account_id FROM ig_handle WHERE account_id IS NOT NULL")}
    links = defaultdict(list)
    for r in conn.execute("SELECT vendor_code, username FROM roster_link"):
        links[r["vendor_code"]].append(r["username"])
    yt_links = defaultdict(list)
    for r in conn.execute("SELECT vendor_code, yt_ref, channel_id FROM roster_youtube"):
        yt_links[r["vendor_code"]].append((r["yt_ref"], r["channel_id"]))
    codes_by_handle = defaultdict(set)
    for code, hs in links.items():
        for h in hs:
            if code:
                codes_by_handle[h].add(code)

    def ig(handle):
        aid = handle_to_account.get(handle)
        if aid is None:
            return None
        if aid not in ig_cache:
            acct = conn.execute("SELECT current_username FROM ig_account WHERE account_id=?", (aid,)).fetchone()
            ig_cache[aid] = instagram_block(conn, aid, acct["current_username"], now, n, days, taxonomy)
        return ig_cache[aid]

    def yt(cid):
        if cid not in yt_cache:
            yt_cache[cid] = youtube_block(conn, cid, now, n, days)
        return yt_cache[cid]

    out = []
    for r in conn.execute("SELECT * FROM roster_creator ORDER BY vendor_code"):
        code = r["vendor_code"]
        handles = links.get(code, [])
        ig_block = next((b for b in (ig(h) for h in handles) if b), None)
        yt_refs = yt_links.get(code, [])
        yt_block = next((yt(cid) for _, cid in yt_refs if cid), None)
        others = sorted({c for h in handles for c in codes_by_handle[h]} - {code})
        out.append({
            "vendor_code": code, "name": r["name"], "tier": r["tier"], "team": r["team"],
            "roster_category": r["category"], "city": r["location"], "gender": r["gender"], "exec_poc": r["exec_poc"],
            "roster_metrics": {"ig_followers": r["roster_followers"], "ig_avg_views": r["roster_avg_views"],
                               "ig_er_pct": r["roster_er"]},
            "other_vendor_codes_same_handle": others,
            "instagram_handle": handles[0] if handles else None,
            "instagram": ig_block,
            "instagram_not_collected": bool(handles) and ig_block is None,
            "youtube_ref": yt_refs[0][0] if yt_refs else None,
            "youtube": yt_block,
        })
    unlinked = [h for h in links.get("", []) if not codes_by_handle.get(h)]
    for h in unlinked:
        out.append({"vendor_code": None, "name": None, "tier": None, "team": None, "roster_category": None,
                    "city": None, "gender": None, "exec_poc": None, "roster_metrics": {},
                    "other_vendor_codes_same_handle": [], "instagram_handle": h, "instagram": ig(h),
                    "instagram_not_collected": ig(h) is None, "youtube_ref": None, "youtube": None})
    return out


def public(profile: dict) -> dict:
    """The profile without the private working lists (posts/evidence), for JSON output."""
    def strip(block):
        return None if block is None else {k: v for k, v in block.items() if not k.startswith("_")}
    return {**profile, "instagram": strip(profile.get("instagram")), "youtube": strip(profile.get("youtube"))}


def days_since(iso_value: str | None, now: datetime) -> int | None:
    t = parse_iso(iso_value) if iso_value else None
    return (now - t).days if t else None


"""CSV exports for review: creators.csv, posts.csv, brand_evidence.csv, run_status.csv."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from . import kb
from .brands import BRAND_WORK_KINDS
from .metrics import summarize
from .topics import creator_themes, load_taxonomy

CREATOR_COLS = [
    "vendor_code", "creator_name", "tier", "team", "roster_category", "roster_city", "instagram_username",
    "profile_url", "status", "last_collected_at", "followers", "followers_source", "followers_observed_at",
    "following", "media_count", "full_name", "is_verified", "biography", "website", "bio_links",
    "posts_in_sample", "sample_from", "sample_to", "posts_last_30_days", "posts_per_week", "formats",
    "median_likes", "median_comments", "median_er_pct", "er_coverage", "avg_reel_views", "median_reel_views",
    "views_coverage", "avg_reel_plays", "plays_coverage", "top_themes", "brands_worked_with",
    "other_collaborators", "brand_mentions", "creator_collabs", "content_locations", "meta_available", "roster_followers", "roster_er_pct",
    "other_vendor_codes_for_handle",
]
POST_COLS = [
    "instagram_username", "vendor_codes", "shortcode", "url", "relation", "owner", "date_ist", "taken_at_utc",
    "date_precision", "format", "caption", "hashtags", "mentions", "coauthors", "tagged_users", "content_location",
    "audio", "likes", "comments", "views", "plays", "metrics_source", "metrics_observed_at", "er_pct", "topics",
    "brand_edges", "surfaces", "post_page_read",
]
EVIDENCE_COLS = [
    "instagram_username", "vendor_codes", "brand_handle", "brand_name", "brand_category", "counterpart_kind",
    "edge_kind", "post_url", "post_date", "evidence_text", "rule_id", "review_status", "source", "first_seen",
    "last_seen",
]
STATUS_COLS = ["run_id", "instagram_username", "vendor_codes", "depth", "status", "posts_in_window", "elapsed_s",
               "meta_requests", "web_requests", "coverage", "error", "finished_at"]


def _j(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return "|".join(str(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _write(path: Path, cols: list[str], rows: list[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})
    return len(rows)


def _status_by_handle(conn) -> dict[str, dict]:
    rows = conn.execute("""SELECT ra.* FROM run_account ra JOIN (
                               SELECT username, MAX(run_id) AS run_id FROM run_account
                               WHERE status <> 'skipped_not_due' GROUP BY username) last USING (username, run_id)""")
    return {r["username"]: dict(r) for r in rows}


def export_all(conn, out_dir: Path, now: datetime, n: int, days: int) -> dict:
    taxonomy = load_taxonomy()
    statuses = _status_by_handle(conn)
    creators, posts_rows, ev_rows = [], [], []
    linked_handles = set()

    for acct in kb.accounts(conn):
        aid, handle = acct["account_id"], acct["current_username"]
        roster = kb.roster_for_handles(conn, acct["handles"])
        vendor_codes = [r["vendor_code"] for r in roster]
        prof = kb.latest_profile(conn, aid)
        posts = kb.window_posts(conn, aid, now, n, days, taxonomy)
        stats = summarize(posts, prof.get("followers"), now)
        evid = kb.evidence(conn, aid)
        # Counts are distinct posts per counterpart. A counterpart is a brand when it is in
        # config/brands.csv or the caption discloses the collaboration; other co-authors/owners
        # are listed separately until someone classifies them.
        def posts_by(pred):
            c = Counter()
            for h, code in {(e["brand_handle"], e["shortcode"]) for e in evid if pred(e)}:
                c[h] += 1
            return c
        disclosed = {e["brand_handle"] for e in evid if e["kind"] in ("collaboration_disclosed", "product_integration")}
        is_work = lambda e: (e["kind"] in BRAND_WORK_KINDS and e["brand_kind"] != "creator"  # noqa: E731
                             and not e["brand_handle"].startswith("("))
        work = posts_by(lambda e: is_work(e) and (e["brand_kind"] == "brand" or e["brand_handle"] in disclosed))
        unclassified = posts_by(lambda e: is_work(e) and e["brand_kind"] != "brand" and e["brand_handle"] not in disclosed)
        mentions = posts_by(lambda e: e["kind"] in ("mention", "hashtag", "event_association") and e["brand_kind"] == "brand")
        collabs = posts_by(lambda e: e["kind"] == "creator_collab")
        locations = Counter(p["location_name"] for p in posts if p.get("location_name"))
        st = statuses.get(handle) or next((statuses[h] for h in acct["handles"] if h in statuses), {})
        meta_av = conn.execute("SELECT meta_available FROM ig_handle WHERE username=?", (handle,)).fetchone()
        base = {
            "instagram_username": handle, "profile_url": f"https://www.instagram.com/{handle}/",
            "status": st.get("status"), "last_collected_at": st.get("finished_at"),
            "followers": prof.get("followers"), "followers_source": prof.get("followers_source"),
            "followers_observed_at": prof.get("followers_observed_at"), "following": prof.get("following"),
            "media_count": prof.get("media_count"), "full_name": prof.get("full_name"),
            "is_verified": prof.get("is_verified"), "biography": prof.get("biography"), "website": prof.get("website"),
            "bio_links": _j([b.get("url") for b in prof.get("bio_links") or []]),
            "posts_in_sample": stats["posts_in_sample"], "sample_from": stats["first_post_at"],
            "sample_to": stats["last_post_at"], "posts_last_30_days": stats["posts_last_30_days"],
            "posts_per_week": stats["posts_per_week"], "formats": _j(stats["formats"]),
            "median_likes": stats["median_likes"], "median_comments": stats["median_comments"],
            "median_er_pct": stats["median_er_pct"], "er_coverage": stats["er_coverage"],
            "avg_reel_views": stats["avg_reel_views"], "median_reel_views": stats["median_reel_views"],
            "views_coverage": stats["views_coverage"], "avg_reel_plays": stats["avg_reel_plays"],
            "plays_coverage": stats["plays_coverage"],
            "top_themes": _j([f"{t} ({c})" for t, c in creator_themes(posts, taxonomy)]),
            "brands_worked_with": _j([f"@{h} ({c})" for h, c in work.most_common()]),
            "other_collaborators": _j([f"@{h} ({c})" for h, c in unclassified.most_common(10)]),
            "brand_mentions": _j([f"@{h} ({c})" for h, c in mentions.most_common(10)]),
            "creator_collabs": _j([f"@{h} ({c})" for h, c in collabs.most_common(10)]),
            "content_locations": _j([f"{l} ({c})" for l, c in locations.most_common(5)]),
            "meta_available": None if not meta_av else meta_av["meta_available"],
        }
        rows = roster or [{}]
        for r in rows:
            linked_handles.add(handle)
            others = [v for v in vendor_codes if v != r.get("vendor_code")]
            creators.append({**base, "vendor_code": r.get("vendor_code"), "creator_name": r.get("name"),
                             "tier": r.get("tier"), "team": r.get("team"), "roster_category": r.get("category"),
                             "roster_city": r.get("location"), "roster_followers": r.get("roster_followers"),
                             "roster_er_pct": r.get("roster_er"), "other_vendor_codes_for_handle": _j(others)})
        edges_by_post = defaultdict(list)
        for e in evid:
            edges_by_post[e["shortcode"]].append(f"{e['kind']}:@{e['brand_handle']}")
        for p in posts:
            posts_rows.append({
                "instagram_username": handle, "vendor_codes": _j(vendor_codes), "shortcode": p["shortcode"],
                "url": p["url"], "relation": p["relation"], "owner": p.get("owner_username"),
                "date_ist": p["date_ist"], "taken_at_utc": p.get("taken_at"),
                "date_precision": "exact" if p.get("taken_at") else ("day" if p.get("approx_date") else ""),
                "format": p.get("format"), "caption": p.get("caption"), "hashtags": _j(p.get("hashtags")),
                "mentions": _j(p.get("mentions")), "coauthors": _j(p.get("coauthors")),
                "tagged_users": _j(p.get("usertags")), "content_location": p.get("location_name"),
                "audio": _j(p.get("audio")), "likes": p["likes"], "comments": p["comments"], "views": p["views"],
                "plays": p["plays"], "metrics_source": p["metrics_source"],
                "metrics_observed_at": p["metrics_observed_at"], "er_pct": p["er_pct"], "topics": _j(p.get("topics")),
                "brand_edges": _j(edges_by_post.get(p["shortcode"])), "surfaces": _j(p.get("surfaces")),
                "post_page_read": "yes" if p.get("detail_fetched_at") else "no",
            })
        for e in evid:
            ev_rows.append({
                "instagram_username": handle, "vendor_codes": _j(vendor_codes), "brand_handle": e["brand_handle"],
                "brand_name": e["brand_name"], "brand_category": e["brand_category"],
                "counterpart_kind": e["brand_kind"], "edge_kind": e["kind"], "post_url": e["url"],
                "post_date": e["taken_at"] or e["approx_date"], "evidence_text": e["evidence_text"],
                "rule_id": e["rule_id"], "review_status": e["review_status"], "source": e["source"],
                "first_seen": e["first_seen"], "last_seen": e["last_seen"],
            })

    # Roster rows whose handle has not been collected yet still get a row (status blank).
    for r in conn.execute("""SELECT rl.username, rc.* FROM roster_link rl LEFT JOIN roster_creator rc USING (vendor_code)
                             LEFT JOIN ig_handle h ON h.username = rl.username
                             WHERE h.account_id IS NULL ORDER BY rl.username"""):
        st = statuses.get(r["username"], {})
        creators.append({"vendor_code": r["vendor_code"], "creator_name": r["name"], "tier": r["tier"],
                         "team": r["team"], "roster_category": r["category"], "roster_city": r["location"],
                         "instagram_username": r["username"], "profile_url": f"https://www.instagram.com/{r['username']}/",
                         "status": st.get("status") or "not_collected", "last_collected_at": st.get("finished_at"),
                         "roster_followers": r["roster_followers"], "roster_er_pct": r["roster_er"]})

    status_rows = []
    last_run = conn.execute("SELECT MAX(run_id) FROM run").fetchone()[0]
    if last_run:
        for r in conn.execute("SELECT * FROM run_account WHERE run_id=? ORDER BY username", (last_run,)):
            req = json.loads(r["requests_json"] or "{}")
            codes = [x["vendor_code"] for x in conn.execute(
                "SELECT vendor_code FROM roster_link WHERE username=? AND vendor_code<>''", (r["username"],))]
            status_rows.append({**dict(r), "instagram_username": r["username"], "vendor_codes": _j(codes),
                                "meta_requests": sum(v for k, v in req.items() if k.startswith("meta:")),
                                "web_requests": sum(v for k, v in req.items() if k.startswith("web:")),
                                "coverage": r["coverage_json"]})
    return {
        "creators.csv": _write(out_dir / "creators.csv", CREATOR_COLS, creators),
        "posts.csv": _write(out_dir / "posts.csv", POST_COLS, posts_rows),
        "brand_evidence.csv": _write(out_dir / "brand_evidence.csv", EVIDENCE_COLS, ev_rows),
        "run_status.csv": _write(out_dir / "run_status.csv", STATUS_COLS, status_rows),
    }

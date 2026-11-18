"""Per-creator knowledge-base folders, generated from the SQLite database.

data/kb/
  README.md                 how to read this folder
  SCHEMA.md                 evidence and metric rules
  index.csv                 one row per creator (vendor code): the table to filter/sort
  index.md                  the same, linked to each creator folder
  brands.csv                one row per brand: creators, posts, last post date
  brands/<handle>.md        every creator linked to that brand, with post links
  creators/<VENDORCODE>__<handle>/
      profile.md            human/LLM-readable summary (every claim links to a post)
      profile.json          the same data, structured (for the chatbot)
      instagram_posts.csv   posts in the window: latest 30 + last 30 days
      youtube_videos.csv    videos in the window
      brand_evidence.csv    both platforms, with the supporting text and rule
      history.csv           followers / subscribers over time

The folder is rebuilt in full on every export (into a temporary folder, then swapped in), so it
never mixes old and new files and a renamed handle never leaves a stale folder behind.
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .profiles import build_profiles, public
from .wiki import SCHEMA

MARKER = ".opraah_kb"

INDEX_COLS = [
    "vendor_code", "name", "tier", "team", "roster_category", "city", "gender", "exec_poc", "folder",
    "ig_handle", "ig_url", "ig_status", "ig_last_collected_at", "ig_metrics_level", "ig_followers",
    "ig_followers_source", "ig_followers_growth_pct_per_30d", "ig_verified", "ig_posts_in_sample",
    "ig_posts_last_30_days", "ig_posts_per_week", "ig_formats", "ig_median_likes", "ig_median_comments",
    "ig_median_er_pct", "ig_er_coverage", "ig_branded_posts", "ig_branded_median_er_pct",
    "ig_organic_median_er_pct", "ig_branded_vs_organic_er_ratio", "ig_consistency", "ig_avg_reel_views",
    "ig_views_coverage", "ig_median_reel_views_per_follower", "ig_avg_reel_plays", "ig_plays_coverage",
    "ig_median_shares", "ig_shares_coverage", "ig_top_posting_days_ist", "ig_top_posting_hours_ist",
    "ig_caption_languages", "ig_top_themes", "ig_content_locations", "ig_bio", "ig_bio_links",
    "yt_ref", "yt_url", "yt_title", "yt_status", "yt_subscribers", "yt_subscribers_growth_pct_per_30d",
    "yt_total_views", "yt_videos_in_sample", "yt_long_videos", "yt_shorts_guess", "yt_uploads_per_week",
    "yt_median_views_long", "yt_median_views_shorts", "yt_median_engagement_pct_of_views",
    "yt_paid_promotion_videos", "yt_last_upload_at", "yt_country",
    "brands_worked_with", "brand_last_post_dates", "other_collaborators", "brand_mentions", "creator_collabs",
    "other_vendor_codes_same_handle", "roster_ig_followers", "roster_ig_er_pct",
]
POST_COLS = ["shortcode", "url", "relation", "owner", "date_ist", "taken_at_utc", "date_precision", "format",
             "caption", "hashtags", "mentions", "coauthors", "tagged_users", "content_location", "audio", "likes",
             "comments", "views", "plays", "shares", "reposts", "er_pct", "is_branded", "topics", "metrics_source",
             "metrics_observed_at", "post_page_read"]
VIDEO_COLS = ["video_id", "url", "published_at", "format", "duration_s", "title", "views", "likes", "comments",
              "engagement_pct_of_views", "paid_promotion", "tags", "category_id", "default_language", "made_for_kids",
              "topics", "metrics_observed_at"]
EVIDENCE_COLS = ["platform", "brand_handle", "brand_name", "brand_category", "counterpart_kind", "edge_kind",
                 "post_url", "post_date", "evidence_text", "rule_id", "review_status", "first_seen", "last_seen"]


def _j(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (list, tuple)):
        return "|".join(_j(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _write_csv(path: Path, cols: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: _j(r.get(k)) for k in cols})


def folder_name(p: dict) -> str:
    code = p["vendor_code"] or "_no_vendor_code"
    social = p.get("instagram_handle") or (p.get("youtube_ref") or "").replace("channel:", "").replace(":", "-")
    raw = f"{code}__{social}" if social else code
    return re.sub(r"[^A-Za-z0-9._@-]+", "_", raw)


def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def _brand_line(b: dict) -> str:
    name = f" ({b['name']})" if b.get("name") else ""
    last = f", last {b['last_post_date']}" if b.get("last_post_date") else ""
    return f"@{b['handle']}{name}: {b['posts']} post(s){last} [{', '.join(b['kinds'])}]"


def flatten(p: dict) -> dict:
    ig, yt = p.get("instagram") or {}, p.get("youtube") or {}
    st, dv, pr = ig.get("stats") or {}, ig.get("derived") or {}, ig.get("profile") or {}
    yst = yt.get("stats") or {}
    brands = (ig.get("brands_worked_with") or []) + (yt.get("brands_worked_with") or [])
    merged: dict[str, dict] = {}
    for b in brands:
        m = merged.setdefault(b["handle"], {"posts": 0, "last": None})
        m["posts"] += b["posts"]
        if b.get("last_post_date") and (m["last"] is None or b["last_post_date"] > m["last"]):
            m["last"] = b["last_post_date"]
    return {
        **{k: p.get(k) for k in ("vendor_code", "name", "tier", "team", "roster_category", "city", "gender", "exec_poc")},
        "folder": folder_name(p),
        "ig_handle": p.get("instagram_handle"), "ig_url": ig.get("url"),
        "ig_status": ig.get("status") or ("not_collected" if p.get("instagram_not_collected") else None),
        "ig_last_collected_at": ig.get("last_collected_at"), "ig_metrics_level": ig.get("metrics_level"),
        "ig_followers": pr.get("followers"), "ig_followers_source": pr.get("followers_source"),
        "ig_followers_growth_pct_per_30d": (ig.get("growth") or {}).get("growth_pct_per_30d"),
        "ig_verified": pr.get("is_verified"), "ig_posts_in_sample": st.get("posts_in_sample"),
        "ig_posts_last_30_days": st.get("posts_last_30_days"), "ig_posts_per_week": st.get("posts_per_week"),
        "ig_formats": st.get("formats"), "ig_median_likes": st.get("median_likes"),
        "ig_median_comments": st.get("median_comments"),
        "ig_median_er_pct": dv.get("median_er_pct_own_posts"), "ig_er_coverage": dv.get("er_own_posts_coverage"),
        "ig_branded_posts": dv.get("branded_posts"), "ig_branded_median_er_pct": dv.get("branded_median_er_pct"),
        "ig_organic_median_er_pct": dv.get("organic_median_er_pct"),
        "ig_branded_vs_organic_er_ratio": dv.get("branded_vs_organic_er_ratio"),
        "ig_consistency": dv.get("consistency"), "ig_avg_reel_views": st.get("avg_reel_views"),
        "ig_views_coverage": st.get("views_coverage"),
        "ig_median_reel_views_per_follower": dv.get("median_reel_views_per_follower"),
        "ig_avg_reel_plays": st.get("avg_reel_plays"), "ig_plays_coverage": st.get("plays_coverage"),
        "ig_median_shares": dv.get("median_shares"), "ig_shares_coverage": dv.get("shares_coverage"),
        "ig_top_posting_days_ist": dv.get("top_posting_days_ist"),
        "ig_top_posting_hours_ist": dv.get("top_posting_hours_ist"),
        "ig_caption_languages": dv.get("caption_languages"),
        "ig_top_themes": [f"{t['theme']} ({t['posts']})" for t in ig.get("themes") or []],
        "ig_content_locations": [f"{l['location']} ({l['posts']})" for l in ig.get("content_locations") or []],
        "ig_bio": pr.get("biography"), "ig_bio_links": pr.get("bio_links"),
        "yt_ref": p.get("youtube_ref"), "yt_url": yt.get("url"), "yt_title": yt.get("title"),
        "yt_status": yt.get("status") or ("not_collected" if p.get("youtube_ref") and not yt else None),
        "yt_subscribers": yt.get("subscribers"),
        "yt_subscribers_growth_pct_per_30d": (yt.get("growth") or {}).get("growth_pct_per_30d"),
        "yt_total_views": yt.get("total_views"), "yt_videos_in_sample": yst.get("videos_in_sample"),
        "yt_long_videos": yst.get("long_videos"), "yt_shorts_guess": yst.get("shorts_guess"),
        "yt_uploads_per_week": yst.get("uploads_per_week"), "yt_median_views_long": yst.get("median_views_long"),
        "yt_median_views_shorts": yst.get("median_views_shorts"),
        "yt_median_engagement_pct_of_views": yst.get("median_engagement_pct_of_views"),
        "yt_paid_promotion_videos": yst.get("paid_promotion_videos"), "yt_last_upload_at": yst.get("last_upload_at"),
        "yt_country": yt.get("country"),
        "brands_worked_with": [f"@{h} ({m['posts']})" for h, m in sorted(merged.items(), key=lambda x: -x[1]["posts"])],
        "brand_last_post_dates": [f"@{h} {m['last']}" for h, m in merged.items() if m["last"]],
        "other_collaborators": [f"@{b['handle']} ({b['posts']})" for b in ig.get("other_collaborators") or []],
        "brand_mentions": [f"@{b['handle']} ({b['posts']})" for b in
                           (ig.get("brand_mentions") or []) + (yt.get("brand_mentions") or [])][:15],
        "creator_collabs": [f"@{b['handle']} ({b['posts']})" for b in ig.get("creator_collabs") or []],
        "other_vendor_codes_same_handle": p.get("other_vendor_codes_same_handle"),
        "roster_ig_followers": (p.get("roster_metrics") or {}).get("ig_followers"),
        "roster_ig_er_pct": (p.get("roster_metrics") or {}).get("ig_er_pct"),
    }


def _post_rows(ig: dict) -> list[dict]:
    rows = []
    for x in ig.get("_posts") or []:
        rows.append({"shortcode": x["shortcode"], "url": x["url"], "relation": x["relation"],
                     "owner": x.get("owner_username"), "date_ist": x.get("date_ist"), "taken_at_utc": x.get("taken_at"),
                     "date_precision": "exact" if x.get("taken_at") else ("day" if x.get("approx_date") else ""),
                     "format": x.get("format"), "caption": x.get("caption"), "hashtags": x.get("hashtags"),
                     "mentions": x.get("mentions"), "coauthors": x.get("coauthors"), "tagged_users": x.get("usertags"),
                     "content_location": x.get("location_name"), "audio": x.get("audio"), "likes": x.get("likes"),
                     "comments": x.get("comments"), "views": x.get("views"), "plays": x.get("plays"),
                     "shares": x.get("shares"), "reposts": x.get("reposts"), "er_pct": x.get("er_pct"),
                     "is_branded": x.get("is_branded"), "topics": x.get("topics"),
                     "metrics_source": x.get("metrics_source"), "metrics_observed_at": x.get("metrics_observed_at"),
                     "post_page_read": bool(x.get("detail_fetched_at"))})
    return rows


def _video_rows(yt: dict) -> list[dict]:
    rows = []
    for v in yt.get("_videos") or []:
        eng = (round((v["likes"] + v["comments"]) / v["views"] * 100, 3)
               if v.get("views") and v.get("likes") is not None and v.get("comments") is not None else None)
        rows.append({**v, "engagement_pct_of_views": eng, "metrics_observed_at": v.get("observed_at"),
                     "paid_promotion": None if v.get("paid_placement") is None else bool(v["paid_placement"]),
                     "made_for_kids": None if v.get("made_for_kids") is None else bool(v["made_for_kids"])})
    return rows


def _evidence_rows(p: dict) -> list[dict]:
    rows = []
    for platform in ("instagram", "youtube"):
        block = p.get(platform) or {}
        for e in block.get("_evidence") or []:
            url = (e.get("url") if platform == "instagram" else f"https://www.youtube.com/watch?v={e['video_id']}")
            rows.append({"platform": platform, "brand_handle": e["brand_handle"], "brand_name": e.get("brand_name"),
                         "brand_category": e.get("brand_category"), "counterpart_kind": e.get("brand_kind"),
                         "edge_kind": e["kind"], "post_url": url,
                         "post_date": (e.get("taken_at") or e.get("approx_date") or e.get("published_at") or "")[:10],
                         "evidence_text": e["evidence_text"], "rule_id": e["rule_id"],
                         "review_status": e["review_status"], "first_seen": e["first_seen"], "last_seen": e["last_seen"]})
    return rows


def render_md(p: dict, now: datetime) -> str:
    ig, yt = p.get("instagram"), p.get("youtube")
    title = p.get("name") or p.get("instagram_handle") or p.get("vendor_code")
    L = [f"# {title}", "",
         f"- Vendor code: `{p['vendor_code'] or '—'}` · tier: {p.get('tier') or '—'} · team: {p.get('team') or '—'}",
         f"- City (roster): {p.get('city') or '—'} · category (roster): {p.get('roster_category') or '—'}",
         f"- Exec POC: {p.get('exec_poc') or '—'}"]
    if p.get("other_vendor_codes_same_handle"):
        L.append(f"- Same Instagram handle also listed under: {', '.join(p['other_vendor_codes_same_handle'])}")
    L += ["", f"_Generated {now.date().isoformat()} from data/opraah.db. Blank (—) means not observed, never zero._", ""]
    L += ["## Instagram", ""]
    if ig:
        pr, st, dv, gr = ig["profile"], ig["stats"], ig["derived"], ig["growth"]
        L += [f"[@{ig['handle']}]({ig['url']}) · status `{ig.get('status')}` · last collected "
              f"{(ig.get('last_collected_at') or '—')[:10]} · metrics from `{ig.get('metrics_level') or '—'}`", "",
              f"- Followers: **{_fmt(pr.get('followers'))}** ({pr.get('followers_source') or '—'}, "
              f"{(pr.get('followers_observed_at') or '—')[:10]})"
              + (f" · growth {gr['growth_pct_per_30d']}% per 30 days since {gr['growth_since']}"
                 if gr.get("growth_pct_per_30d") is not None else ""),
              f"- Bio: {(pr.get('biography') or '—').replace(chr(10), ' ')}"]
        if pr.get("bio_links"):
            L.append("- Links: " + ", ".join(pr["bio_links"]))
        L += ["", "| Metric | Value | Coverage |", "|---|---:|---|",
              f"| Posts in sample | {_fmt(st['posts_in_sample'])} ({_j(st['formats'])}) | {st['posts_last_30_days']} in last 30 days |",
              f"| Posts per week | {_fmt(st['posts_per_week'])} | |",
              f"| Median likes | {_fmt(st['median_likes'])} | {st['er_coverage']} |",
              f"| Median comments | {_fmt(st['median_comments'])} | {st['er_coverage']} |",
              f"| Median ER % (own posts) | {_fmt(dv['median_er_pct_own_posts'])} | {dv['er_own_posts_coverage']} |",
              f"| Branded vs organic ER % | {_fmt(dv['branded_median_er_pct'])} vs {_fmt(dv['organic_median_er_pct'])} "
              f"(ratio {_fmt(dv['branded_vs_organic_er_ratio'])}) | {dv['branded_er_coverage']} |",
              f"| Consistency (likes spread) | {_fmt(dv['consistency'])} | cv {_fmt(dv['likes_cv'])} |",
              f"| Avg Reel views (Meta) | {_fmt(st['avg_reel_views'])} | {st['views_coverage']} |",
              f"| Reel views per follower | {_fmt(dv['median_reel_views_per_follower'])} | |",
              f"| Avg Reel plays (public) | {_fmt(st['avg_reel_plays'])} | {st['plays_coverage']} |",
              f"| Median shares | {_fmt(dv['median_shares'])} | {dv['shares_coverage']} |",
              f"| Best posting days / hours (IST) | {_fmt(dv['top_posting_days_ist'])} / {_fmt(dv['top_posting_hours_ist'])} | |",
              f"| Caption languages | {_fmt(dv['caption_languages'])} | |", "",
              "Themes: " + (", ".join(f"{t['theme']} ({t['posts']})" for t in ig["themes"]) or "—"),
              "Content locations (geotags): " + (", ".join(f"{l['location']} ({l['posts']})"
                                                           for l in ig["content_locations"]) or "—"), ""]
        for key, label in (("brands_worked_with", "Brands worked with"), ("other_collaborators", "Other collaborators (unclassified)"),
                           ("creator_collabs", "Roster creators collaborated with"), ("brand_mentions", "Brand mentions")):
            if ig.get(key):
                L += [f"**{label}**", ""] + [f"- {_brand_line(b)}" for b in ig[key][:15]] + [""]
        L += ["Every brand claim is listed with its post in `brand_evidence.csv`; every post in `instagram_posts.csv`.", ""]
    elif p.get("instagram_handle"):
        L += [f"@{p['instagram_handle']}: not collected yet.", ""]
    else:
        L += ["No Instagram handle on the roster.", ""]
    L += ["## YouTube", ""]
    if yt:
        ys, gr = yt["stats"], yt["growth"]
        L += [f"[{yt.get('title') or yt['channel_id']}]({yt['url']}) · status `{yt.get('status')}` · "
              f"last collected {(yt.get('last_collected_at') or '—')[:10]}", "",
              f"- Subscribers: **{_fmt(yt.get('subscribers'))}** · total views {_fmt(yt.get('total_views'))} · "
              f"videos {_fmt(yt.get('video_count'))} · country {yt.get('country') or '—'}"
              + (f" · growth {gr['growth_pct_per_30d']}% per 30 days" if gr.get("growth_pct_per_30d") is not None else ""),
              f"- Sample: {ys['videos_in_sample']} videos ({ys['long_videos']} long, {ys['shorts_guess']} shorts by duration, "
              f"{ys['live']} live); {ys['videos_last_30_days']} in last 30 days; {_fmt(ys['uploads_per_week'])} per week",
              f"- Median views: long {_fmt(ys['median_views_long'])} · shorts {_fmt(ys['median_views_shorts'])}",
              f"- Median engagement (likes+comments)/views: {_fmt(ys['median_engagement_pct_of_views'])}% ({ys['engagement_coverage']})",
              f"- Videos marked 'includes paid promotion': {ys['paid_promotion_videos']}", ""]
        if yt.get("brands_worked_with"):
            L += ["**Brands (YouTube)**", ""] + [f"- {_brand_line(b)}" for b in yt["brands_worked_with"][:15]] + [""]
    elif p.get("youtube_ref"):
        L += [f"{p['youtube_ref']}: not collected yet.", ""]
    else:
        L += ["No YouTube link on the roster.", ""]
    return "\n".join(L) + "\n"


README = """# Opraah creator knowledge base

Generated from `data/opraah.db` by `python -m opraah_scraper export`. Do not edit files here:
they are rebuilt on every export. Change `config/brands.csv`, the roster, or review statuses in
the database instead.

- `index.csv`: one row per creator (vendor code). Filter and sort here.
- `creators/<VENDORCODE>__<handle>/`: everything about one creator
  - `profile.md` (read this), `profile.json` (same data for software or the chatbot)
  - `instagram_posts.csv`, `youtube_videos.csv`: the posts behind every number
  - `brand_evidence.csv`: each brand claim with its post link and the supporting text
  - `history.csv`: followers and subscribers over time
- `brands.csv` and `brands/<handle>.md`: who has worked with each brand
- `SCHEMA.md`: how metrics and brand evidence are defined
"""


def build(conn, out_dir: Path, now: datetime, n: int = 30, days: int = 30) -> dict:
    profiles = build_profiles(conn, now, n, days)
    tmp = out_dir.with_name(out_dir.name + f".tmp-{os.getpid()}")
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "creators").mkdir(parents=True)
    (tmp / "brands").mkdir()
    (tmp / MARKER).write_text("generated by opraah_scraper; safe to delete and rebuild\n")
    (tmp / "README.md").write_text(README, encoding="utf-8")
    (tmp / "SCHEMA.md").write_text(SCHEMA, encoding="utf-8")
    index_rows, brand_rows = [], defaultdict(lambda: {"creators": set(), "posts": 0, "last": None, "name": None,
                                                       "category": None, "items": []})
    used_names: set[str] = set()
    for p in profiles:
        name = folder_name(p)
        while name in used_names:
            name += "_"
        used_names.add(name)
        d = tmp / "creators" / name
        d.mkdir()
        flat = flatten(p) | {"folder": name}
        index_rows.append(flat)
        (d / "profile.md").write_text(render_md(p, now), encoding="utf-8")
        (d / "profile.json").write_text(json.dumps(public(p), ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        _write_csv(d / "instagram_posts.csv", POST_COLS, _post_rows(p.get("instagram") or {}))
        _write_csv(d / "youtube_videos.csv", VIDEO_COLS, _video_rows(p.get("youtube") or {}))
        ev = _evidence_rows(p)
        _write_csv(d / "brand_evidence.csv", EVIDENCE_COLS, ev)
        hist = [{"date": t[:10], "platform": "instagram", "metric": "followers", "value": v}
                for t, v in (p.get("instagram") or {}).get("_snapshots") or [] if v is not None]
        if p.get("youtube"):
            for r in conn.execute("SELECT observed_at, subscribers, total_views FROM yt_channel_snapshot "
                                  "WHERE channel_id=? ORDER BY observed_at", (p["youtube"]["channel_id"],)):
                hist.append({"date": r["observed_at"][:10], "platform": "youtube", "metric": "subscribers",
                             "value": r["subscribers"]})
        _write_csv(d / "history.csv", ["date", "platform", "metric", "value"], hist)
        who = p["vendor_code"] or p.get("instagram_handle")
        for e in ev:
            if e["edge_kind"] in ("collaboration_disclosed", "coauthor", "collab_post_owned_by_brand",
                                  "product_integration") and e["counterpart_kind"] != "creator" \
                    and not e["brand_handle"].startswith("("):
                b = brand_rows[e["brand_handle"]]
                b["creators"].add(who)
                b["posts"] += 1
                b["name"], b["category"] = e["brand_name"], e["brand_category"]
                b["last"] = max(filter(None, [b["last"], e["post_date"]]), default=None)
                b["items"].append((who, name, e))
    _write_csv(tmp / "index.csv", INDEX_COLS, index_rows)
    lines = ["# Creators", "", "| Creator | Tier | City | IG followers | IG median ER % | YT subscribers | Brands |",
             "|---|---|---|---:|---:|---:|---|"]
    for r in index_rows:
        lines.append(f"| [{r['name'] or r['ig_handle'] or r['vendor_code']}](creators/{r['folder']}/profile.md) | "
                     f"{r['tier'] or ''} | {r['city'] or ''} | {_fmt(r['ig_followers'])} | {_fmt(r['ig_median_er_pct'])} | "
                     f"{_fmt(r['yt_subscribers'])} | {_j(r['brands_worked_with'][:4])} |")
    (tmp / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    brows = []
    for handle, b in sorted(brand_rows.items(), key=lambda x: -len(x[1]["creators"])):
        brows.append({"brand_handle": handle, "brand_name": b["name"], "category": b["category"],
                      "creators": len(b["creators"]), "posts": b["posts"], "last_post_date": b["last"],
                      "creator_list": sorted(b["creators"])})
        md = [f"# @{handle}" + (f" · {b['name']}" if b["name"] else ""), "", f"Category: {b['category'] or '—'}", "",
              "| Creator | Platform | Edge | Post | Evidence |", "|---|---|---|---|---|"]
        for who, folder, e in sorted(b["items"], key=lambda x: x[2]["post_date"] or "", reverse=True):
            md.append(f"| [{who}](../creators/{folder}/profile.md) | {e['platform']} | {e['edge_kind']} | "
                      f"[{e['post_date']}]({e['post_url']}) | {(e['evidence_text'] or '')[:90].replace('|', '/')} |")
        (tmp / "brands" / f"{re.sub(r'[^A-Za-z0-9._-]', '_', handle)}.md").write_text("\n".join(md) + "\n",
                                                                                      encoding="utf-8")
    _write_csv(tmp / "brands.csv", ["brand_handle", "brand_name", "category", "creators", "posts", "last_post_date",
                                    "creator_list"], brows)
    # Swap in atomically-ish: old → .old, tmp → out, delete old.
    old = out_dir.with_name(out_dir.name + ".old")
    if old.exists():
        shutil.rmtree(old)
    if out_dir.exists():
        out_dir.rename(old)
    tmp.rename(out_dir)
    if old.exists():
        shutil.rmtree(old)
    return {"creators": len(index_rows), "brands": len(brows), "profiles": profiles, "index_rows": index_rows}


def sync_to(src: Path, dest: Path) -> dict:
    """Mirror data/kb into another folder (e.g. a Google Drive for desktop folder).

    Only files that changed are copied; files in `dest` that no longer exist in `src` are removed,
    but only inside a folder this tool created (it carries the .opraah_kb marker), so pointing it at
    the wrong folder can't delete anything else.
    """
    dest.mkdir(parents=True, exist_ok=True)
    if any(dest.iterdir()) and not (dest / MARKER).exists():
        raise SystemExit(f"{dest} is not empty and was not created by this tool; choose an empty or new folder.")
    copied = removed = 0
    src_files = {f.relative_to(src) for f in src.rglob("*") if f.is_file()}
    for rel in src_files:
        s, d = src / rel, dest / rel
        if d.exists() and d.stat().st_size == s.stat().st_size and d.read_bytes() == s.read_bytes():
            continue
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, d)
        copied += 1
    for f in sorted(dest.rglob("*"), reverse=True):
        rel = f.relative_to(dest)
        if f.is_file() and rel not in src_files:
            f.unlink()
            removed += 1
        elif f.is_dir() and not any(f.iterdir()):
            f.rmdir()
    return {"copied": copied, "removed": removed}

"""Read-side views over the SQLite knowledge base (used by exports, the wiki and later the chatbot)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .metrics import engagement_rate, select_window, summarize
from .topics import post_topics
from .util import loads, parse_iso

IST = timezone(timedelta(hours=5, minutes=30))
SOURCE_PRIORITY = {"meta_bd": 3, "web_post": 2, "web_reels": 1}


def accounts(conn) -> list[dict]:
    """Every collected account with its handles and roster links."""
    rows = conn.execute("""SELECT a.account_id, a.current_username, a.ig_pk, a.graph_id, a.first_seen, a.last_seen
                           FROM ig_account a ORDER BY a.current_username""").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["handles"] = [x["username"] for x in conn.execute(
            "SELECT username FROM ig_handle WHERE account_id=? ORDER BY last_seen DESC", (r["account_id"],))]
        out.append(d)
    return out


def roster_for_handles(conn, handles: list[str]) -> list[dict]:
    if not handles:
        return []
    q = ",".join("?" * len(handles))
    rows = conn.execute(f"""SELECT DISTINCT rl.vendor_code, rc.* FROM roster_link rl
                            LEFT JOIN roster_creator rc ON rc.vendor_code = rl.vendor_code
                            WHERE rl.username IN ({q}) AND rl.vendor_code <> '' ORDER BY rl.vendor_code""", handles)
    return [dict(r) for r in rows]


def latest_profile(conn, account_id: int) -> dict:
    rows = conn.execute("""SELECT * FROM profile_snapshot WHERE account_id=? ORDER BY observed_at DESC, id DESC""",
                        (account_id,)).fetchall()
    if not rows:
        return {}
    merged: dict = {}
    for r in rows:  # newest first; fill gaps from older/other-source snapshots
        for k in r.keys():
            if merged.get(k) is None and r[k] is not None:
                merged[k] = r[k]
                if k == "followers":
                    merged["followers_source"] = r["source"]
                    merged["followers_observed_at"] = r["observed_at"]
    newest = max(rows, key=lambda r: (r["observed_at"], r["source"] == "meta_bd"))
    if newest["followers"] is not None:
        merged["followers"] = newest["followers"]
        merged["followers_source"] = newest["source"]
        merged["followers_observed_at"] = newest["observed_at"]
    merged["bio_links"] = loads(merged.pop("bio_links_json", None), [])
    return merged


def latest_metrics(conn, shortcode: str) -> dict:
    rows = conn.execute("SELECT * FROM post_metric WHERE shortcode=? ORDER BY observed_at DESC, id DESC",
                        (shortcode,)).fetchall()
    out = {"likes": None, "comments": None, "views": None, "plays": None, "shares": None, "reposts": None,
           "metrics_source": None, "metrics_observed_at": None}
    if not rows:
        return out
    latest_ts = rows[0]["observed_at"]
    same = sorted([r for r in rows if r["observed_at"] == latest_ts],
                  key=lambda r: SOURCE_PRIORITY.get(r["source"], 0), reverse=True)
    for r in same + [r for r in rows if r["observed_at"] != latest_ts]:
        if out["likes"] is None and r["likes"] is not None:
            out["likes"], out["metrics_source"], out["metrics_observed_at"] = r["likes"], r["source"], r["observed_at"]
        if out["comments"] is None and r["comments"] is not None:
            out["comments"] = r["comments"]
        if out["views"] is None and r["views"] is not None:
            out["views"] = r["views"]
        if out["plays"] is None and r["plays"] is not None:
            out["plays"] = r["plays"]
        for k in ("shares", "reposts"):
            if k in r.keys() and out[k] is None and r[k] is not None:
                out[k] = r[k]
    return out


def account_posts(conn, account_id: int) -> list[dict]:
    rows = conn.execute("""SELECT p.*, ap.relation, ap.surfaces_json FROM account_post ap JOIN post p USING (shortcode)
                           WHERE ap.account_id=?""", (account_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for f in ("hashtags", "mentions", "coauthors", "usertags", "audio"):
            d[f] = loads(d.pop(f + "_json"), None)
        d["surfaces"] = loads(d.pop("surfaces_json"), [])
        d["date"] = d.get("taken_at") or d.get("approx_date")
        out.append(d)
    return out


def window_posts(conn, account_id: int, now: datetime, n: int, days: int, taxonomy=None) -> list[dict]:
    posts = account_posts(conn, account_id)
    keep = select_window([{"shortcode": p["shortcode"], "taken_at": p["date"]} for p in posts], now, n, days)
    by = {p["shortcode"]: p for p in posts}
    prof = latest_profile(conn, account_id)
    out = []
    for code in keep:
        p = by[code]
        p.update(latest_metrics(conn, code))
        p["er_pct"] = engagement_rate(p["likes"], p["comments"], prof.get("followers"))
        p["url"] = p.get("permalink") or f"https://www.instagram.com/{'reel' if p.get('format') == 'reel' else 'p'}/{code}/"
        if taxonomy is not None:
            p["topics"] = post_topics(p, taxonomy)
        dt = parse_iso(p["date"])
        p["date_ist"] = dt.astimezone(IST).strftime("%Y-%m-%d %H:%M") if dt and p.get("taken_at") else (
            dt.strftime("%Y-%m-%d") if dt else "")
        out.append(p)
    return out


def evidence(conn, account_id: int | None = None) -> list[dict]:
    q = """SELECT be.*, b.name AS brand_name, b.category AS brand_category, b.kind AS brand_kind,
                  p.taken_at, p.approx_date, p.permalink, p.format, a.current_username
           FROM brand_evidence be
           LEFT JOIN brand b ON b.handle = be.brand_handle
           JOIN post p ON p.shortcode = be.shortcode
           JOIN ig_account a ON a.account_id = be.account_id"""
    rows = conn.execute(q + (" WHERE be.account_id=?" if account_id else "") +
                        " ORDER BY COALESCE(p.taken_at, p.approx_date) DESC", (account_id,) if account_id else ())
    out = []
    for r in rows:
        d = dict(r)
        d["url"] = d["permalink"] or f"https://www.instagram.com/p/{d['shortcode']}/"
        out.append(d)
    return out


def summary(conn, account_id: int, now: datetime, n: int, days: int) -> dict:
    prof = latest_profile(conn, account_id)
    posts = window_posts(conn, account_id, now, n, days)
    return {"profile": prof, "posts": posts, "stats": summarize(posts, prof.get("followers"), now)}

"""End-to-end collection on the GamerFleet fixtures (fake Instaloader context, fake Meta)."""

import csv
import json
from datetime import datetime

import instaloader.exceptions as ie
import pytest

from opraah_scraper import db, wiki
from opraah_scraper.brands import Registry
from opraah_scraper.collect import Collector
from opraah_scraper.export import export_all
from opraah_scraper.roster import load_inputs
from opraah_scraper.sources.meta_bd import MetaResult
from opraah_scraper.sources.web import WebClient
from opraah_scraper.util import iso

from conftest import NOW, FakeContext, quiet_pacer


class FakeMeta:
    """Business Discovery stand-in: owned posts only, Reel views, no co-authors/locations."""

    def __init__(self, posts, outcome="ok"):
        self.posts = [p for p in posts if p["owner"] == "gamerfleetog"]
        self.outcome = outcome
        self.calls = 0

    def fetch(self, username, now, n=30, days=30):
        self.calls += 1
        if self.outcome != "ok":
            return MetaResult(outcome=self.outcome)
        from opraah_scraper.parse.text import hashtags, mentions
        out = []
        for p in sorted(self.posts, key=lambda r: r["date_utc"], reverse=True)[:50]:
            out.append({"shortcode": p["shortcode"], "graph_media_id": "m" + p["shortcode"],
                        "taken_at": iso(datetime.fromisoformat(p["date_utc"])),
                        "format": p["type"], "product_type": "reels" if p["type"] == "reel" else "feed",
                        "caption": p["caption"], "hashtags": hashtags(p["caption"]), "mentions": mentions(p["caption"]),
                        "permalink": p["url"], "likes": p["likes"], "comments": p["comments"],
                        "views": p["likes"] * 20 if p["type"] == "reel" else None, "metrics_hidden": False})
        prof = {"username": "gamerfleetog", "graph_id": "17841401061997974", "followers": 2815790, "following": 100,
                "media_count": 1100, "full_name": "Anshu Bisht", "biography": "Gamer", "website": None,
                "bio_links": None, "is_verified": None, "is_private": False, "profile_pic_url": None}
        return MetaResult(outcome="ok", profile=prof, posts=out, pages=1, exhausted=True, raw_pages=[{"x": 1}])


def make(conn, settings, gf_posts, gf_codes, meta=None, run_id=None, roster=()):
    ctx = FakeContext(gf_posts, gf_codes)
    if run_id is None:
        run_id, _ = db.open_run(conn, "run", f"k{datetime.now().timestamp()}", [], {}, resume=False)
    c = Collector(conn, settings, run_id, registry=Registry.load(), roster_handles=set(roster),
                  now_fn=lambda: NOW, log=lambda m: None)
    web = WebClient(quiet_pacer(), recorder=c.recorder)
    web._ctx = ctx
    c.web, c.meta = web, meta
    return c, ctx


def counts(conn):
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("ig_account", "post", "account_post", "brand_evidence", "post_metric", "profile_snapshot")}


def kinds(conn):
    return {(r["brand_handle"], r["kind"]) for r in conn.execute("SELECT brand_handle, kind FROM brand_evidence")}


def test_web_only_full_collection(conn, settings, gf_posts, gf_codes):
    c, ctx = make(conn, settings, gf_posts, gf_codes)
    rep = c.collect("gamerfleetog", "full")
    assert rep.status == "ok", rep.coverage
    assert rep.posts_in_window == 30
    assert rep.requests["web:profile_page"] == 1 and rep.requests["web:timeline_page"] == 2
    assert rep.requests["web:post_page"] == 30          # no Meta: every window post needs its page
    k = kinds(conn)
    assert ("lenovolegionindia", "coauthor") in k
    assert ("lenovolegionindia", "collaboration_disclosed") in k
    assert ("lenovolegionindia", "mention") not in k     # superseded by the stronger edges
    assert ("youtubeindia", "collab_post_owned_by_brand") in k
    assert ("samsungindia", "collaboration_disclosed") in k
    assert ("amazonnews_in", "coauthor") in k
    rel = conn.execute("SELECT relation FROM account_post WHERE shortcode='Ddbe0z9FB_2'").fetchone()[0]
    assert rel == "coauthor"
    # Old pins (Feb 2026 / Nov 2024) are not in the latest-30 window.
    window = [r["shortcode"] for r in conn.execute("SELECT DISTINCT shortcode FROM brand_evidence")]
    assert "DU5l5xuCQAZ" not in window
    # Geotag kept as content location.
    locs = {r[0] for r in conn.execute("SELECT location_name FROM post WHERE location_name IS NOT NULL")}
    assert locs == {"Colma, California", "Las Vegas, Nevada"}
    # Both platform ids on one account.
    a = conn.execute("SELECT ig_pk, graph_id FROM ig_account").fetchall()
    assert [tuple(x) for x in a] == [("1790371399", "17841401061997974")]


def test_meta_plus_web_reads_only_needed_post_pages(conn, settings, gf_posts, gf_codes):
    meta = FakeMeta(gf_posts)
    c, ctx = make(conn, settings, gf_posts, gf_codes, meta=meta)
    rep = c.collect("gamerfleetog", "full")
    assert rep.status == "ok", rep.coverage
    assert rep.posts_in_window == 30
    pages = [x for x in ctx.calls if x.startswith("p/")]
    assert 0 < len(pages) < 15                    # branded/collab posts only
    assert "p/Ddbe0z9FB_2/" in pages               # collab post owned by youtubeindia (not in Meta)
    assert "p/DeZIkL6JjzH/" in pages               # Lenovo: needs co-author field
    assert "web:reels_page" not in rep.requests    # Meta supplied Reel views
    assert ("lenovolegionindia", "coauthor") in kinds(conn)
    # One account even though Meta and web each identified it.
    assert conn.execute("SELECT COUNT(*) FROM ig_account").fetchone()[0] == 1
    views = conn.execute("SELECT COUNT(*) FROM post_metric WHERE source='meta_bd' AND views IS NOT NULL").fetchone()[0]
    assert views > 0


def test_light_run_with_meta_makes_no_web_requests(conn, settings, gf_posts, gf_codes):
    c, ctx = make(conn, settings, gf_posts, gf_codes, meta=FakeMeta(gf_posts))
    rep = c.collect("gamerfleetog", "light")
    assert rep.status == "ok"
    assert ctx.calls == [] and rep.requests == {}


def test_meta_unavailable_falls_back_to_web(conn, settings, gf_posts, gf_codes):
    c, ctx = make(conn, settings, gf_posts, gf_codes, meta=FakeMeta(gf_posts, outcome="unavailable"))
    rep = c.collect("gamerfleetog", "full")
    assert rep.status == "ok"
    assert rep.coverage["meta_bd"] == "unavailable"
    assert conn.execute("SELECT meta_available FROM ig_handle WHERE username='gamerfleetog'").fetchone()[0] == 0


def test_rerun_is_idempotent(conn, settings, gf_posts, gf_codes):
    c, _ = make(conn, settings, gf_posts, gf_codes, meta=FakeMeta(gf_posts))
    c.collect("gamerfleetog", "full")
    first = counts(conn)
    ids = conn.execute("SELECT account_id FROM ig_account").fetchall()
    c2, ctx2 = make(conn, settings, gf_posts, gf_codes, meta=FakeMeta(gf_posts))
    c2.collect("gamerfleetog", "full")
    second = counts(conn)
    for t in ("ig_account", "post", "account_post", "brand_evidence"):
        assert second[t] == first[t], t
    assert second["post_metric"] > first["post_metric"]          # a new observation per run
    assert conn.execute("SELECT account_id FROM ig_account").fetchall() == ids
    assert not [x for x in ctx2.calls if x.startswith("p/")]     # post pages are cached


def test_interrupt_and_resume(conn, settings, gf_posts, gf_codes):
    c, ctx = make(conn, settings, gf_posts, gf_codes)
    ctx.interrupt_after = 10
    with pytest.raises(KeyboardInterrupt):
        c.collect("gamerfleetog", "full")
    fetched = conn.execute("SELECT COUNT(*) FROM post WHERE detail_fetched_at IS NOT NULL").fetchone()[0]
    assert fetched >= 5                                           # committed as it went
    c2, ctx2 = make(conn, settings, gf_posts, gf_codes, run_id=c.run_id)
    rep = c2.collect("gamerfleetog", "full")
    assert rep.status == "ok"
    assert len([x for x in ctx2.calls if x.startswith("p/")]) == 30 - fetched


def test_rate_limit_mid_run_gives_partial_and_stays_due(conn, settings, gf_posts, gf_codes):
    c, ctx = make(conn, settings, gf_posts, gf_codes)
    c.web.pacer.wait_on_limit = False
    ctx.fail_always["p/DeZIkL6JjzH/"] = ie.TooManyRequestsException("429")
    rep = c.collect("gamerfleetog", "full")
    assert rep.status == "partial"
    assert rep.coverage["web_post"]["stopped"] == "rate_limited"
    row = conn.execute("SELECT last_full_at, last_status FROM ig_handle WHERE username='gamerfleetog'").fetchone()
    assert row["last_full_at"] is None and row["last_status"] == "partial"   # retried by the next --due run
    rep2 = c.collect("other.creator", "full")
    assert rep2.status == "rate_limited"                          # web source stopped for this run


def test_private_and_missing_accounts(conn, settings, gf_posts, gf_codes):
    c, ctx = make(conn, settings, gf_posts, gf_codes)
    assert c.collect("no.such.user", "full").status == "not_found"
    orig = ctx.get_page_data

    def private_page(path):
        data = orig("gamerfleetog/") if path == "private.one/" else orig(path)
        for b in data:
            if "xig_user_by_username" in b:
                b["xig_user_by_username"]["is_private"] = True
                b["xig_user_by_username"]["username"] = "private.one"
        return data
    ctx.get_page_data = private_page
    assert c.collect("private.one", "full").status == "private"


def test_exports_and_wiki(tmp_path, conn, settings, gf_posts, gf_codes):
    roster = tmp_path / "roster.csv"
    roster.write_text("Vendor Code,Creator Name,Type,Location,Instagram\n"
                      "OPEX-1,Anshu Bisht,Exclusive,Dehradun,https://www.instagram.com/gamerfleetog/\n"
                      "OPPAR-2,Anshu B,Non-Exclusive,Dehradun,@gamerfleetog\n"
                      "OPPAR-3,Not yet,Non-Exclusive,Pune,https://www.instagram.com/someone.else/\n")
    db.import_roster(conn, load_inputs([roster]))
    c, _ = make(conn, settings, gf_posts, gf_codes, meta=FakeMeta(gf_posts))
    rep = c.collect("gamerfleetog", "full")
    db.save_run_account(conn, c.run_id, "gamerfleetog", depth="full", status=rep.status)
    out = tmp_path / "exports"
    n = export_all(conn, out, NOW, 30, 30)
    creators = list(csv.DictReader((out / "creators.csv").open(encoding="utf-8-sig")))
    gf = [r for r in creators if r["instagram_username"] == "gamerfleetog"]
    assert sorted(r["vendor_code"] for r in gf) == ["OPEX-1", "OPPAR-2"]     # shared handle: two rows
    assert gf[0]["followers"] == "2815790" and gf[0]["roster_city"] == "Dehradun"
    assert "@lenovolegionindia" in gf[0]["brands_worked_with"]
    assert gf[0]["other_vendor_codes_for_handle"] in ("OPEX-1", "OPPAR-2")
    other = [r for r in creators if r["instagram_username"] == "someone.else"]
    assert other and other[0]["status"] == "not_collected"
    posts = list(csv.DictReader((out / "posts.csv").open(encoding="utf-8-sig")))
    assert len(posts) == 30 and len({p["shortcode"] for p in posts}) == 30
    assert all(p["url"].startswith("https://www.instagram.com/") for p in posts)
    ev = list(csv.DictReader((out / "brand_evidence.csv").open(encoding="utf-8-sig")))
    assert all(e["post_url"] and e["evidence_text"] and e["rule_id"] for e in ev)
    w = wiki.build(conn, tmp_path / "wiki", NOW, 30, 30)
    page = (tmp_path / "wiki" / "creators" / "gamerfleetog.md").read_text()
    assert "OPEX-1" in page and "lenovolegionindia" in page and "https://www.instagram.com/" in page
    assert (tmp_path / "wiki" / "brands" / "lenovolegionindia.md").exists()
    assert w["creators"] == 1 and n["posts.csv"] == 30

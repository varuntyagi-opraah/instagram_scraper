"""YouTube collector, derived analytics, per-creator folders, migration, lock and due rules."""

import csv
import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from opraah_scraper import db, kb_export
from opraah_scraper.cli import RunLock, _depth_for, capability
from opraah_scraper.collect_youtube import collect_refs
from opraah_scraper.derive import caption_language, growth, instagram_derived
from opraah_scraper.roster import load_inputs
from opraah_scraper.sources.meta_bd import MetaBusinessDiscovery
from opraah_scraper.sources.youtube import QuotaExhausted, YouTubeClient, iso_duration_s
from opraah_scraper.util import iso

from conftest import NOW, quiet_pacer
from test_collect import FakeMeta, make
from test_sources import FakeResp, FakeSession, bd_page


# ---------------------------------------------------------------- YouTube

def yt_channel(handle="@wackyteen", cid="UCabcdefghijklmnopqrstuv"):
    return FakeResp(200, {"items": [{
        "id": cid,
        "snippet": {"title": "Wacky Teen", "customUrl": handle, "publishedAt": "2019-01-01T00:00:00Z",
                    "country": "IN", "description": "Gaming"},
        "statistics": {"subscriberCount": "496000", "viewCount": "90000000", "videoCount": "300",
                       "hiddenSubscriberCount": False},
        "contentDetails": {"relatedPlaylists": {"uploads": "UU" + cid[2:]}},
        "brandingSettings": {"channel": {"keywords": "gaming bgmi"}},
        "topicDetails": {"topicCategories": ["https://en.wikipedia.org/wiki/Video_game_culture"]}}]})


def yt_playlist(n=35, token=None):
    return FakeResp(200, {"items": [{"contentDetails": {
        "videoId": f"vid{i:08d}", "videoPublishedAt": iso(NOW - timedelta(days=i * 3))}} for i in range(n)],
        **({"nextPageToken": token} if token else {})})


def yt_videos(n=35):
    items = []
    for i in range(n):
        items.append({
            "id": f"vid{i:08d}",
            "snippet": {"publishedAt": iso(NOW - timedelta(days=i * 3)),
                        "title": f"BGMI clutch {i}" + (" | #ad with @boat.nirvana" if i == 2 else ""),
                        "description": "Use code WACKY for 10% off" if i == 2 else "gameplay", "tags": ["bgmi"],
                        "categoryId": "20", "defaultAudioLanguage": "hi", "liveBroadcastContent": "none"},
            "statistics": {"viewCount": str(10000 + i), "likeCount": str(500 + i), "commentCount": "20"},
            "contentDetails": {"duration": "PT45S" if i % 2 else "PT12M3S"},
            "status": {"madeForKids": False},
            "paidProductPlacementDetails": {"hasPaidProductPlacement": i == 2},
        })
    return FakeResp(200, {"items": items})


def test_iso_duration():
    assert iso_duration_s("PT45S") == 45 and iso_duration_s("PT1H2M3S") == 3723 and iso_duration_s("P1DT1S") == 86401


def test_youtube_fetch_costs_three_units():
    session = FakeSession([yt_channel(), yt_playlist(), yt_videos()])
    c = YouTubeClient("K", quiet_pacer("youtube"), session=session)
    res = c.fetch("@wackyteen", NOW)
    assert res.outcome == "ok" and res.units == 3 and len(res.videos) == 35
    assert res.channel["subscribers"] == 496000 and res.channel["uploads_playlist"].startswith("UU")
    v2 = next(v for v in res.videos if v["video_id"] == "vid00000002")
    assert v2["paid_placement"] is True and v2["format"] == "video"
    assert next(v for v in res.videos if v["video_id"] == "vid00000001")["format"] == "short_guess"
    assert session.calls[0]["forHandle"] == "@wackyteen"


def test_youtube_legacy_c_link_needs_search_flag():
    c = YouTubeClient("K", quiet_pacer("youtube"), session=FakeSession([]))
    res = c.fetch("c:mistertikku", NOW)
    assert res.outcome == "not_found" and "allow-search" in res.detail and res.units == 0


def test_youtube_quota_exceeded_stops():
    err = FakeResp(403, {"error": {"errors": [{"reason": "quotaExceeded"}]}})
    c = YouTubeClient("K", quiet_pacer("youtube"), session=FakeSession([err]))
    with pytest.raises(QuotaExhausted):
        c.fetch("@x", NOW)


def test_youtube_local_daily_cap():
    c = YouTubeClient("K", quiet_pacer("youtube"), daily_units=10, units_used_today=lambda: 10,
                      session=FakeSession([]))
    with pytest.raises(QuotaExhausted):
        c.fetch("@x", NOW)


def _roster(tmp_path):
    f = tmp_path / "roster.csv"
    f.write_text("Vendor Code,Creator Name,Type,Location,Instagram,YouTube\n"
                 "OPEX-1,Anshu Bisht,Exclusive,Dehradun,https://www.instagram.com/gamerfleetog/,https://www.youtube.com/@wackyteen\n"
                 "OPPAR-2,Anshu B,Agency talent,Dehradun,@gamerfleetog,\n"
                 "OPPAR-3,Only YT,Non-Exclusive,Pune,,https://youtube.com/c/legacyname\n", encoding="utf-8")
    return f


def test_youtube_collect_store_and_evidence(tmp_path, conn):
    imp = load_inputs([_roster(tmp_path)])
    db.import_roster(conn, imp)
    refs = list(dict.fromkeys(r.youtube_ref for r in imp.rows if r.youtube_ref))
    assert refs == ["@wackyteen", "c:legacyname"]
    client = YouTubeClient("K", quiet_pacer("youtube"), session=FakeSession([yt_channel(), yt_playlist(), yt_videos()]))
    reps = collect_refs(conn, client, refs, None, lambda: NOW, log=lambda m: None)
    assert [r.status for r in reps] == ["ok", "not_found"]
    assert conn.execute("SELECT channel_id FROM roster_youtube WHERE vendor_code='OPEX-1'").fetchone()[0]
    assert conn.execute("SELECT COUNT(*) FROM yt_video").fetchone()[0] == 35
    kinds = {(r[0], r[1]) for r in conn.execute("SELECT brand_handle, kind FROM yt_brand_evidence")}
    assert ("boat.nirvana", "collaboration_disclosed") in kinds
    assert ("boat.nirvana", "product_integration") in kinds


# ---------------------------------------------------------------- derived analytics

def test_caption_language():
    assert caption_language("Meri behen mera hekdi nikalti hai par apna skincare") == "hinglish"
    assert caption_language("Powering up my gaming experience with the new laptop") == "english"
    assert caption_language("दिवाली की शुभकामनाएं सबको") == "hindi"
    assert caption_language("🔥🔥🔥 #fyp") is None


def test_growth_needs_a_week():
    snaps = [(iso(NOW - timedelta(days=40)), 1000), (iso(NOW - timedelta(days=2)), 1100), (iso(NOW), 1200)]
    g = growth(snaps)
    assert g["growth_abs"] == 200 and g["growth_pct"] == 20.0 and g["growth_pct_per_30d"] == 15.0
    assert growth([(iso(NOW - timedelta(days=2)), 1), (iso(NOW), 2)])["growth_pct"] is None


def test_branded_vs_organic():
    posts = [{"shortcode": f"P{i}", "relation": "owner", "likes": 100 if i < 3 else 50, "comments": 0,
              "taken_at": iso(NOW - timedelta(days=i)), "format": "image", "caption": "hello there friends"}
             for i in range(10)]
    d = instagram_derived(posts, 1000, branded={"P0", "P1", "P2"})
    assert d["branded_median_er_pct"] == 10.0 and d["organic_median_er_pct"] == 5.0
    assert d["branded_vs_organic_er_ratio"] == 2.0 and d["branded_posts"] == 3


# ---------------------------------------------------------------- knowledge-base folders

def test_kb_folders(tmp_path, conn, settings, gf_posts, gf_codes):
    db.import_roster(conn, load_inputs([_roster(tmp_path)]))
    c, _ = make(conn, settings, gf_posts, gf_codes, meta=FakeMeta(gf_posts))
    c.collect("gamerfleetog", "full")
    client = YouTubeClient("K", quiet_pacer("youtube"), session=FakeSession([yt_channel(), yt_playlist(), yt_videos()]))
    collect_refs(conn, client, ["@wackyteen"], None, lambda: NOW, log=lambda m: None)
    out = tmp_path / "kb"
    res = kb_export.build(conn, out, NOW)
    folders = sorted(p.name for p in (out / "creators").iterdir())
    assert folders == ["OPEX-1__gamerfleetog", "OPPAR-2__gamerfleetog", "OPPAR-3__c-legacyname"]
    d = out / "creators" / "OPEX-1__gamerfleetog"
    assert {f.name for f in d.iterdir()} == {"profile.md", "profile.json", "instagram_posts.csv",
                                             "youtube_videos.csv", "brand_evidence.csv", "history.csv"}
    prof = json.loads((d / "profile.json").read_text())
    assert prof["instagram"]["profile"]["followers"] == 2815790
    assert prof["youtube"]["subscribers"] == 496000 and prof["other_vendor_codes_same_handle"] == ["OPPAR-2"]
    assert "_posts" not in prof["instagram"]
    md = (d / "profile.md").read_text()
    assert "## YouTube" in md and "lenovolegionindia" in md and "Branded vs organic" in md
    rows = list(csv.DictReader((out / "index.csv").open(encoding="utf-8-sig")))
    r1 = next(r for r in rows if r["vendor_code"] == "OPEX-1")
    assert r1["ig_followers"] == "2815790" and r1["yt_subscribers"] == "496000"
    assert "@lenovolegionindia" in r1["brands_worked_with"] and "@boat.nirvana" in r1["brands_worked_with"]
    assert next(r for r in rows if r["vendor_code"] == "OPPAR-3")["yt_status"] == "not_collected"
    ev = list(csv.DictReader((d / "brand_evidence.csv").open(encoding="utf-8-sig")))
    assert {e["platform"] for e in ev} == {"instagram", "youtube"}
    assert (out / "brands" / "lenovolegionindia.md").exists()
    assert not any(p.name.startswith("kb.") for p in tmp_path.iterdir())     # temp/old folders cleaned up
    assert res["creators"] == 3


def test_sync_to_is_safe(tmp_path):
    src = tmp_path / "kb"
    (src / "creators" / "A").mkdir(parents=True)
    (src / kb_export.MARKER).write_text("x")
    (src / "creators" / "A" / "profile.md").write_text("one")
    dest = tmp_path / "drive" / "kb"
    assert kb_export.sync_to(src, dest) == {"copied": 2, "removed": 0}
    (src / "creators" / "A" / "profile.md").write_text("two")
    (dest / "creators" / "B").mkdir()
    (dest / "creators" / "B" / "stale.md").write_text("old")
    assert kb_export.sync_to(src, dest) == {"copied": 1, "removed": 1}
    assert not (dest / "creators" / "B").exists()
    other = tmp_path / "my_documents"
    other.mkdir()
    (other / "important.docx").write_text("keep me")
    with pytest.raises(SystemExit):
        kb_export.sync_to(src, other)
    assert (other / "important.docx").exists()


# ---------------------------------------------------------------- migration, Meta fields, lock, due

def test_v1_database_upgrades_in_place(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript((Path(db.__file__).parent / "schema.sql").read_text())
    old.execute("PRAGMA user_version = 1")
    old.execute("INSERT INTO ig_handle (username, first_seen, last_seen, last_full_at) VALUES ('a','t','t','t')")
    old.commit()
    old.close()
    conn = db.connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(post_metric)")}
    assert {"shares", "reposts"} <= cols
    assert conn.execute("SELECT metrics_level FROM ig_handle").fetchone()[0] == "none"
    assert conn.execute("SELECT COUNT(*) FROM yt_video").fetchone()[0] == 0
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_meta_drops_shares_field_when_rejected():
    reject = FakeResp(400, {"error": {"code": 100, "message": "(#100) Tried accessing nonexisting field (shares_count)"}})
    session = FakeSession([reject, bd_page(5, after=None)])
    client = MetaBusinessDiscovery("T", "1784", "v27.0", quiet_pacer("meta"), session=session)
    res = client.fetch("gamerfleetog", NOW)
    assert res.outcome == "ok"
    assert "shares_count" in session.calls[0]["fields"] and "shares_count" not in session.calls[1]["fields"]


def test_meta_shares_stored_when_served():
    page = bd_page(3, after=None)
    for m in page._body["business_discovery"]["media"]["data"]:
        m["shares_count"] = 42
    client = MetaBusinessDiscovery("T", "1784", "v27.0", quiet_pacer("meta"), session=FakeSession([page]))
    assert {p["shares"] for p in client.fetch("x", NOW).posts} == {42}


def test_run_lock(tmp_path):
    with RunLock(tmp_path / ".run.lock"):
        with pytest.raises(SystemExit):
            with RunLock(tmp_path / ".run.lock"):
                pass
    (tmp_path / ".run.lock").write_text("999999")          # stale pid from a crashed run
    with RunLock(tmp_path / ".run.lock"):
        pass


def test_no_metrics_accounts_become_due_when_better_source_exists(conn, settings):
    ts = iso(NOW - timedelta(days=1))
    conn.execute("INSERT INTO ig_handle (username, first_seen, last_seen, last_full_at, metrics_level) "
                 "VALUES ('a', ?, ?, ?, 'none')", (ts, ts, ts))
    args = SimpleNamespace(no_meta=False, no_web=False, detail="none")
    assert capability(settings, args) == "none"
    assert _depth_for(conn, "a", settings, True, None, NOW, "none") == "none"      # nothing better: not due
    assert _depth_for(conn, "a", settings, True, None, NOW, "web") == "full"       # post pages available
    settings.meta_access_token, settings.meta_ig_user_id = "T", "1"
    assert capability(settings, args) == "meta"


def test_youtube_own_handles_are_not_brand_evidence(tmp_path, conn):
    imp = load_inputs([_roster(tmp_path)])
    db.import_roster(conn, imp)
    vids = yt_videos(3)
    vids._body["items"][0]["snippet"]["description"] = "Join @wackyteen | Instagram @gamerfleetog | with @someone.else"
    client = YouTubeClient("K", quiet_pacer("youtube"), session=FakeSession([yt_channel(), yt_playlist(3), vids]))
    collect_refs(conn, client, ["@wackyteen"], None, lambda: NOW, log=lambda m: None)
    handles = {r[0] for r in conn.execute("SELECT brand_handle FROM yt_brand_evidence")}
    assert "someone.else" in handles and "wackyteen" not in handles and "gamerfleetog" not in handles

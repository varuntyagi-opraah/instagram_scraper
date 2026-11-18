from datetime import timedelta

from opraah_scraper.brands import Registry, derive_edges
from opraah_scraper.metrics import engagement_rate, select_window, summarize, window_satisfied
from opraah_scraper.parse.text import hashtags, mentions

from conftest import NOW


def _post(row):
    return {"shortcode": row["shortcode"], "caption": row["caption"], "owner_username": row["owner"],
            "coauthors": row["coauthors"], "hashtags": hashtags(row["caption"]), "mentions": mentions(row["caption"]),
            "usertags": []}


def _edges(rows, code, roster=frozenset()):
    row = next(r for r in rows if r["shortcode"] == code)
    return {(e.brand_handle, e.kind): e for e in derive_edges(_post(row), "gamerfleetog", Registry.load(), set(roster))}


def test_lenovo_coauthor_and_disclosure(gf_posts):
    e = _edges(gf_posts, "DeZIkL6JjzH")
    assert ("lenovolegionindia", "coauthor") in e
    assert ("lenovolegionindia", "collaboration_disclosed") in e
    assert "#Collaboration" in e[("lenovolegionindia", "collaboration_disclosed")].evidence_text


def test_samsung_disclosure_named_only_by_hashtag(gf_posts):
    e = _edges(gf_posts, "DeopzBlJ9fO")   # "#Collaboration ... #GalaxyZFold8", no @mention
    assert ("samsungindia", "collaboration_disclosed") in e


def test_porsche_hashtag_is_a_mention_not_a_deal(gf_posts):
    e = _edges(gf_posts, "DCl-wJOvHEA")
    assert ("porsche", "hashtag") in e
    assert not any(k == "collaboration_disclosed" for _, k in e)
    # "Event @launchcontrol.ind" -> event association, flagged for review
    assert e[("launchcontrol.ind", "event_association")].review_status == "needs_review"


def test_post_owned_by_other_account(gf_posts):
    e = _edges(gf_posts, "Ddbe0z9FB_2")   # published by youtubeindia, gamerfleetog co-author
    assert ("youtubeindia", "collab_post_owned_by_brand") in e
    assert ("saregama_official", "mention") in e


def test_roster_creator_becomes_creator_collab(gf_posts):
    e = _edges(gf_posts, "Ddbe0z9FB_2", roster={"rohitraghv_"})
    assert ("rohitraghv_", "creator_collab") in e
    assert ("katariaaryann", "coauthor") in e


def test_plain_mention_without_disclosure(gf_posts):
    e = _edges(gf_posts, "Deg3TfWJ2O0")   # "@joy_beautifulbynature skincare use"
    assert ("joy_beautifulbynature", "mention") in e
    assert not any(k == "collaboration_disclosed" for _, k in e)


def _dated(n, start_days_ago=0, step=1, prefix="P"):
    return [{"shortcode": f"{prefix}{i}", "taken_at": (NOW - timedelta(days=start_days_ago + i * step)).isoformat()}
            for i in range(n)]


def test_window_old_pins_do_not_displace_recent_posts():
    pins = _dated(3, start_days_ago=400, prefix="PIN")
    recent = _dated(40, step=2)
    keep = select_window(pins + recent, NOW, 30, 30)
    assert len(keep) == 30
    assert not any(c.startswith("PIN") for c in keep)
    recent_pin = [{"shortcode": "PINNEW", "taken_at": (NOW - timedelta(hours=3)).isoformat()}]
    keep = select_window(recent_pin + recent, NOW, 30, 30)
    assert keep[:2] == ["P0", "PINNEW"]   # ordered by time; the recent pin stays eligible
    assert len(keep) == 30


def test_window_union_of_latest_30_and_last_30_days():
    busy = _dated(45, step=0.5)          # 45 posts in ~22 days: all within 30 days
    assert len(select_window(busy, NOW, 30, 30)) == 45
    sparse = _dated(10, step=20)          # 10 posts over 200 days: latest 30 = all 10
    assert len(select_window(sparse, NOW, 30, 30)) == 10


def test_window_satisfied_needs_pin_slack_and_old_last_entry():
    dates = [p["taken_at"] for p in _dated(32, step=2)]
    assert not window_satisfied(dates, NOW, 30, 30)       # only 32 < 33
    dates = [p["taken_at"] for p in _dated(33, step=2)]
    assert window_satisfied(dates, NOW, 30, 30)            # last is 64 days old
    assert not window_satisfied(dates[:-1] + [None], NOW, 30, 30)


def test_metrics_keep_missing_values_missing():
    assert engagement_rate(None, 10, 1000) is None
    assert engagement_rate(90, 10, 1000) == 10.0
    posts = [{"format": "reel", "likes": 100, "comments": 10, "views": 5000, "plays": None,
              "taken_at": NOW.isoformat()}] + \
            [{"format": "reel", "likes": None, "comments": 5, "views": None, "plays": 900,
              "taken_at": (NOW - timedelta(days=i)).isoformat()} for i in range(1, 21)]
    s = summarize(posts, 1000, NOW)
    assert s["views_coverage"] == "1 of 21 Reels"
    assert s["plays_coverage"] == "20 of 21 Reels"
    assert s["er_coverage"] == "1 of 21 posts"
    assert s["median_er_pct"] == 11.0
    assert s["avg_reel_views"] == 5000

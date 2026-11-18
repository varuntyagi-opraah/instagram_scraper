from opraah_scraper.parse import web
from opraah_scraper.parse.embedded_json import page_data
from opraah_scraper.parse.text import date_from_accessibility_caption, hashtags, mentions

from conftest import FIX, GF


def test_profile_page_fields_and_ids():
    prof = web.parse_profile(page_data((GF / "profile.html").read_text()), "gamerfleetog")
    assert prof["followers"] == 2815790
    assert prof["ig_pk"] == "1790371399"
    assert prof["graph_id"] == "17841401061997974"  # matches Business Discovery's id
    assert prof["is_private"] is False
    nodes, info = web.timeline_nodes(prof["timeline"])
    assert len(nodes) == 12 and info["has_next_page"] is True
    first = nodes[0]
    assert first["shortcode"] == "DU5l5xuCQAZ" and first["format"] == "reel"
    assert first["approx_date"] == "2026-02-18T00:00:00+00:00"
    assert first["owner_username"] == "gamerfleetog"
    assert "gamerfleet" in first["hashtags"]


def test_reels_tab_has_plays_not_captions():
    prof = web.parse_profile(page_data((GF / "reels.html").read_text()), "gamerfleetog")
    reels, _ = web.reels_nodes(prof["clips"])
    assert len(reels) == 12
    assert all(r["plays"] is not None for r in reels)
    assert reels[0]["plays"] == 58093073 and reels[0]["likes"] == 2782710


def test_tagged_tab_is_login_wall():
    blocks = page_data((GF / "tagged.html").read_text())
    assert web.is_login_wall(blocks)
    assert not web.is_login_wall(page_data((GF / "profile.html").read_text()))


def test_post_page_from_real_reel():
    blocks = page_data((FIX / "reel_remo" / "reel.html").read_text())
    media = web.find_media(blocks, "DfwvVj7y1o-")
    post, m = web.parse_post(media)
    assert post["owner_username"] == "remo.rizz"
    assert post["format"] == "reel"
    assert post["coauthors"] == []          # observed: none (not "unknown")
    assert post["taken_at"].startswith("2026-11-15")
    assert m == {"likes": 39974, "comments": 209, "plays": None}  # logged-out post pages expose no views
    assert post["audio"]["original"] is True
    assert post["permalink"] == "https://www.instagram.com/reel/DfwvVj7y1o-/"


def test_text_helpers():
    cap = "#Collaboration Powering up with the Lenovo Legion 5 😎 #lenovo @lenovolegionindia. email a@b.com"
    assert hashtags(cap) == ["collaboration", "lenovo"]
    assert mentions(cap) == ["lenovolegionindia"]
    assert date_from_accessibility_caption("Video by X on November 2, 2026. May be") == "2026-11-02T00:00:00+00:00"
    assert date_from_accessibility_caption(None) is None

"""Meta client (synthetic responses shaped on Meta's docs) and web client error handling."""

import json

import instaloader.exceptions as ie
import pytest

from opraah_scraper.budget import Outcome, SourceStopped
from opraah_scraper.sources.meta_bd import MetaBusinessDiscovery, classify_error, usage_pct
from opraah_scraper.sources.web import WebClient

from conftest import NOW, FakeContext, quiet_pacer


class FakeResp:
    def __init__(self, status, body, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        if self._body is None:
            raise ValueError
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        return self.responses.pop(0)


def bd_page(n, start=0, after="CUR", hide_likes=()):
    data = []
    for i in range(start, start + n):
        m = {"id": f"1790{i}", "caption": f"post {i} #collab @samsungindia" if i == 1 else f"post {i}",
             "timestamp": f"2026-11-{16 - (i % 15):02d}T10:00:00+0000",
             "media_type": "VIDEO" if i % 2 == 0 else "CAROUSEL_ALBUM",
             "media_product_type": "REELS" if i % 2 == 0 else "FEED",
             "permalink": f"https://www.instagram.com/{'reel' if i % 2 == 0 else 'p'}/CODE{i:07d}/",
             "comments_count": 10 + i}
        if i not in hide_likes:
            m["like_count"] = 100 + i
        if i % 2 == 0:
            m["view_count"] = 5000 + i
        data.append(m)
    body = {"business_discovery": {"id": "17841401061997974", "username": "gamerfleetog", "name": "Anshu Bisht",
                                   "followers_count": 2815790, "follows_count": 100, "media_count": 1100,
                                   "biography": "Gamer", "website": "https://youtube.com/@gamerfleet",
                                   "media": {"data": data, "paging": {"cursors": {"after": after}}}}, "id": "1784"}
    return FakeResp(200, body, {"x-app-usage": json.dumps({"call_count": 10, "total_time": 3, "total_cputime": 2})})


def test_meta_normalizes_and_paginates():
    session = FakeSession([bd_page(50, hide_likes={3})])
    client = MetaBusinessDiscovery("T", "1784", "v27.0", quiet_pacer("meta"), session=session, max_pages=1)
    res = client.fetch("gamerfleetog", NOW)
    assert res.outcome == "ok" and len(res.posts) == 50
    assert res.profile["followers"] == 2815790 and res.profile["graph_id"] == "17841401061997974"
    p0, p3 = res.posts[0], res.posts[3]
    assert p0["format"] == "reel" and p0["views"] == 5000 and p0["shortcode"] == "CODE0000000"
    assert p3["likes"] is None and p3["metrics_hidden"] is True       # hidden likes stay unknown
    assert "business_discovery.username(gamerfleetog)" in session.calls[0]["fields"]
    assert ".after(" not in session.calls[0]["fields"]


def test_meta_second_page_uses_cursor_and_stops_at_end():
    session = FakeSession([bd_page(50, after="C1"), bd_page(10, start=50, after=None)])
    client = MetaBusinessDiscovery("T", "1784", "v27.0", quiet_pacer("meta"), session=session)
    # dates in bd_page cycle within November, so the window is never "old enough": pagination continues to the end
    res = client.fetch("gamerfleetog", NOW)
    assert res.pages == 2 and res.exhausted and len(res.posts) == 60
    assert ".after(C1)" in session.calls[1]["fields"]


@pytest.mark.parametrize("status,body,expected", [
    (400, {"error": {"code": 110, "error_subcode": 2207013, "message": "Cannot find User"}}, "unavailable"),
    (400, {"error": {"code": 4, "message": "Application request limit reached"}}, "rate_limited"),
    (400, {"error": {"code": 80002, "message": "too many calls"}}, "rate_limited"),
    (400, {"error": {"code": 190, "message": "Error validating access token"}}, "auth_error"),
    (500, {"error": {"code": 1, "message": "unknown"}}, "error"),
])
def test_meta_error_mapping(status, body, expected):
    assert classify_error(status, body)[0] == expected


def test_meta_unavailable_account():
    body = {"error": {"code": 110, "error_subcode": 2207013, "message": "Cannot find User"}}
    client = MetaBusinessDiscovery("T", "1784", "v27.0", quiet_pacer("meta"), session=FakeSession([FakeResp(400, body)]))
    assert client.fetch("personal.account", NOW).outcome == "unavailable"


def test_meta_rate_limit_pauses_then_retries():
    limit = FakeResp(400, {"error": {"code": 4, "message": "limit"}})
    pacer = quiet_pacer("meta")
    client = MetaBusinessDiscovery("T", "1784", "v27.0", pacer, session=FakeSession([limit, bd_page(5, after=None)]))
    res = client.fetch("gamerfleetog", NOW)
    assert res.outcome == "ok"
    assert 15 * 60 in pacer.sleeps


def test_meta_auth_error_stops_source():
    bad = FakeResp(400, {"error": {"code": 190, "message": "expired"}})
    pacer = quiet_pacer("meta")
    client = MetaBusinessDiscovery("T", "1784", "v27.0", pacer, session=FakeSession([bad]))
    assert client.fetch("a", NOW).outcome == "auth_error"
    with pytest.raises(SourceStopped):
        client.fetch("b", NOW)


def test_usage_header_parsing():
    h = {"x-business-use-case-usage": json.dumps({"1784": [{"type": "instagram", "call_count": 96,
                                                             "total_cputime": 5, "total_time": 7,
                                                             "estimated_time_to_regain_access": 12}]})}
    assert usage_pct(h) == (96.0, 12)


# ---------------------------------------------------------------- web

def _web(gf_posts, gf_codes, **pacer_kw):
    ctx = FakeContext(gf_posts, gf_codes)
    client = WebClient(quiet_pacer(**pacer_kw))
    client._ctx = ctx
    return client, ctx


def test_web_profile_timeline_reels_post(gf_posts, gf_codes):
    client, ctx = _web(gf_posts, gf_codes)
    prof = client.profile_page("gamerfleetog")
    assert prof.outcome == "ok" and prof.data["followers"] == 2815790
    page = client.timeline_page("gamerfleetog", prof.data["graph_id"], prof.data["timeline"]["page_info"]["end_cursor"])
    assert page.outcome == "ok" and len(page.data) == 12
    reels = client.reels_page("gamerfleetog")
    assert reels.outcome == "ok" and reels.data[0]["plays"] == 58093073
    post = client.post_page("gamerfleetog", "DeZIkL6JjzH")
    p, m = post.data
    assert p["coauthors"] == ["lenovolegionindia"] and m["likes"] is not None


def test_web_429_pauses_then_retries(gf_posts, gf_codes):
    client, ctx = _web(gf_posts, gf_codes)
    ctx.fail["gamerfleetog/"] = ie.TooManyRequestsException("429 Too Many Requests")
    assert client.profile_page("gamerfleetog").outcome == "ok"
    assert client.pacer.sleeps.count(15 * 60) == 1


def test_web_repeated_refusals_stop_source(gf_posts, gf_codes):
    client, ctx = _web(gf_posts, gf_codes)
    ctx.fail_always["gamerfleetog/"] = ie.ConnectionException("401 Unauthorized - Please wait a few minutes")
    assert client.profile_page("gamerfleetog").outcome == "rate_limited"   # 15 min pause, retry, refused
    assert client.profile_page("gamerfleetog").outcome == "rate_limited"   # 30 min pause, retry, refused -> 60
    with pytest.raises(SourceStopped):
        client.profile_page("gamerfleetog")
    assert client.pacer.stopped_reason


def test_web_login_wall_stops_immediately(gf_posts, gf_codes):
    client, ctx = _web(gf_posts, gf_codes)
    ctx.username = "gamerfleetog"
    # Serve the login wall page in place of the profile.
    ctx.get_page_data = lambda path: __import__("opraah_scraper.parse.embedded_json", fromlist=["x"]).page_data(
        (__import__("conftest").GF / "tagged.html").read_text())
    assert client.profile_page("gamerfleetog").outcome == "login_wall"
    with pytest.raises(SourceStopped):
        client.profile_page("gamerfleetog")


def test_web_redirect_to_login_is_login_wall(gf_posts, gf_codes):
    client, ctx = _web(gf_posts, gf_codes)
    ctx.fail["p/DeZIkL6JjzH/"] = ie.LoginRequiredException("Redirected to login page.")
    assert client.post_page("gamerfleetog", "DeZIkL6JjzH").outcome == "login_wall"


def test_web_not_found(gf_posts, gf_codes):
    client, _ = _web(gf_posts, gf_codes)
    assert client.profile_page("no.such.user").outcome == "not_found"
    assert client.pacer.stopped_reason is None

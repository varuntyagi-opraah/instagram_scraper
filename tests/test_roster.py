from pathlib import Path

import pytest

from opraah_scraper import db
from opraah_scraper.roster import load_inputs, normalize_handle

ROSTER_DIR = Path.home() / "Downloads" / "opraah_data"


@pytest.mark.parametrize("value,expected", [
    ("https://www.instagram.com/thewackyteenn/", "thewackyteenn"),
    ("https://instagram.com/MisterTikku", "mistertikku"),
    ("instagram.com/gamerfleetog?hl=en", "gamerfleetog"),
    ("https://www.instagram.com/gamerfleetog/?hl=en", "gamerfleetog"),
    ("https://www.instagram.com/heyaman.27/reels/", "heyaman.27"),
    ("www.instagram.com/_u/some_user", "some_user"),
    ("@Remo.Rizz", "remo.rizz"),
    ("  remo.rizz  ", "remo.rizz"),
])
def test_normalize_valid(value, expected):
    assert normalize_handle(value) == (expected, None)


@pytest.mark.parametrize("value,problem", [
    ("https://www.instagram.com/p/DfwvVj7y1o-/", "post_or_non_profile_url"),
    ("https://www.instagram.com/reel/DfwvVj7y1o-/", "post_or_non_profile_url"),
    ("https://www.youtube.com/@wackyteen", "not_an_instagram_url"),
    ("bad handle!", "invalid_handle_characters"),
    ("a..b", "invalid_handle_characters"),
])
def test_normalize_invalid(value, problem):
    assert normalize_handle(value) == (None, problem)


@pytest.mark.parametrize("value", ["", "NA", "n/a", "-", None])
def test_normalize_missing(value):
    assert normalize_handle(value) == (None, None)


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_shared_handle_kept_on_both_vendor_codes(tmp_path, conn):
    ex = _write(tmp_path, "exclusive-roster.csv",
                "Vendor Code,Creator Name,Type,Location,Instagram\n"
                "OPEX-1,Sonal,Exclusive,Delhi,https://www.instagram.com/thesonalraghuvanshi/\n"
                "OPEX-2,No IG,Exclusive,Pune,\n")
    nx = _write(tmp_path, "non-exclusive-roster.csv",
                "Vendor Code,Creator Name,Type,Location,Instagram\n"
                "OPPAR-9,Sonal R,Non-Exclusive,Delhi,@TheSonalRaghuvanshi\n"
                "OPPAR-8,Other,Non-Exclusive,Mumbai,https://www.instagram.com/p/XYZ12345/\n")
    imp = load_inputs([ex, nx])
    s = imp.summary()
    assert s["rows"] == 4 and s["distinct_handles"] == 1
    assert s["rows_without_handle"] == 1 and s["rows_with_invalid_handle"] == 1
    assert imp.shared_handles == {"thesonalraghuvanshi": ["OPEX-1", "OPPAR-9"]}
    assert imp.both_tiers == ["thesonalraghuvanshi"]
    db.import_roster(conn, imp)
    links = conn.execute("SELECT vendor_code FROM roster_link WHERE username='thesonalraghuvanshi' ORDER BY 1").fetchall()
    assert [r[0] for r in links] == ["OPEX-1", "OPPAR-9"]
    assert conn.execute("SELECT COUNT(*) FROM roster_creator").fetchone()[0] == 4
    # Re-importing is idempotent.
    db.import_roster(conn, imp)
    assert conn.execute("SELECT COUNT(*) FROM roster_link").fetchone()[0] == 2
    issues = {r["vendor_code"]: r["issues"] for r in imp.review_rows()}
    assert "shared_handle_with:OPPAR-9" in issues["OPEX-1"]
    assert issues["OPEX-2"] == "missing_instagram"
    assert issues["OPPAR-8"] == "post_or_non_profile_url"


def test_text_input(tmp_path):
    t = _write(tmp_path, "handles.txt", "# pilot\n@gamerfleetog\nhttps://www.instagram.com/remo.rizz/\n\ngamerfleetog\n")
    imp = load_inputs([t])
    assert imp.handles == ["gamerfleetog", "remo.rizz"]
    assert all(r.vendor_code == "" for r in imp.rows)


def test_username_column_csv(tmp_path):
    p = _write(tmp_path, "u.csv", "username\nGamerFleetOG\n")
    assert load_inputs([p]).handles == ["gamerfleetog"]


@pytest.mark.skipif(not (ROSTER_DIR / "exclusive-roster-2026-11-16.csv").exists(), reason="roster exports not present")
def test_real_roster_counts():
    imp = load_inputs([ROSTER_DIR / "exclusive-roster-2026-11-16.csv", ROSTER_DIR / "non-exclusive-roster-2026-11-16.csv"])
    s = imp.summary()
    assert s["rows"] == 1964
    # The "non-exclusive" export also carries agency talent (same split as the Roster pages).
    assert s["by_tier"] == {"exclusive": 222, "non_exclusive": 337, "agency_talent": 1405}
    assert s["distinct_handles"] == 1393
    assert s["rows_without_handle"] == 523
    assert s["rows_with_invalid_handle"] == 0
    assert s["handles_shared_by_vendor_codes"] == 46
    assert s["handles_in_both_tiers"] == 32   # across exclusive / non-exclusive / agency talent
    assert s["rows_with_youtube"] > 0


def test_dotenv_inline_comments(tmp_path):
    from opraah_scraper.config import load_settings
    env = tmp_path / ".env"
    env.write_text('OPRAAH_WEB_FETCHER=obscura   # post pages only\nMETA_ACCESS_TOKEN="EAAG#x"\nMETA_IG_USER_ID=1784\n')
    s = load_settings(env)
    assert s.web_fetcher == "obscura" and s.meta_access_token == "EAAG#x" and s.meta_configured


@pytest.mark.parametrize("value,expected", [
    ("https://www.youtube.com/@wackyteen", "@wackyteen"),
    ("https://youtube.com/c/mistertikku", "c:mistertikku"),
    ("https://www.youtube.com/channel/UC1234567890123456789012", "channel:UC1234567890123456789012"),
    ("https://www.youtube.com/user/CarryMinati", "user:carryminati"),
    ("https://m.youtube.com/@Some.One?si=x", "@some.one"),
    ("", None), ("NA", None), ("https://instagram.com/x", None),
])
def test_normalize_youtube(value, expected):
    from opraah_scraper.roster import normalize_youtube
    assert normalize_youtube(value) == expected


def test_agency_and_profile_only_tiers(tmp_path):
    p = _write(tmp_path, "c.csv", "Instagram,Vendor Code,Type\n@a1,V1,Agency talent\n@b1,V2,Profile only (not in roster exports)\n")
    assert [r.tier for r in load_inputs([p]).rows] == ["agency_talent", "profile_only"]

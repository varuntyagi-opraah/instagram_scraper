"""SQLite access. All writes go through these helpers so reruns stay idempotent."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .roster import RosterImport
from .util import dumps, iso, loads, utcnow

SCHEMA_VERSION = 2
RELATION_RANK = {"owner": 3, "coauthor": 2, "profile_association": 1}
# Columns added after v1: (table, column, type). Added only when missing, so an existing
# database upgrades in place and old rows keep NULL ("not observed").
ADDED_COLUMNS = [
    ("post_metric", "shares", "INTEGER"),
    ("post_metric", "reposts", "INTEGER"),
    ("ig_handle", "metrics_level", "TEXT"),   # none | web | meta: best metrics source of the last full run
    ("roster_creator", "youtube_raw", "TEXT"),
]


def _columns(conn, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def migrate(conn: sqlite3.Connection) -> None:
    here = Path(__file__).parent
    conn.executescript((here / "schema.sql").read_text(encoding="utf-8"))
    conn.executescript((here / "schema_v2.sql").read_text(encoding="utf-8"))
    for table, col, typ in ADDED_COLUMNS:
        if col not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            if (table, col) == ("ig_handle", "metrics_level"):
                # Backfill from what is stored, so accounts collected before v2 without likes/comments
                # are recognised and refreshed once a better source (Meta / post pages) is available.
                conn.execute("""UPDATE ig_handle SET metrics_level = CASE
                    WHEN EXISTS (SELECT 1 FROM account_post ap JOIN post_metric m USING (shortcode)
                                 WHERE ap.account_id = ig_handle.account_id AND m.source = 'meta_bd') THEN 'meta'
                    WHEN EXISTS (SELECT 1 FROM account_post ap JOIN post_metric m USING (shortcode)
                                 WHERE ap.account_id = ig_handle.account_id AND m.likes IS NOT NULL
                                   AND m.source = 'web_post') THEN 'web'
                    ELSE 'none' END
                    WHERE last_full_at IS NOT NULL""")
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
        migrate(conn)
    return conn


def now() -> str:
    return iso(utcnow())


# ----------------------------------------------------------------- roster

def import_roster(conn: sqlite3.Connection, imp: RosterImport) -> dict:
    ts = now()
    creators = links = 0
    with conn:
        for r in imp.rows:
            if r.vendor_code:
                conn.execute(
                    """INSERT INTO roster_creator (vendor_code, name, tier, team, category, location, gender,
                           exec_poc, roster_followers, roster_avg_views, roster_er, raw_json, source_file, imported_at,
                           youtube_raw)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(vendor_code) DO UPDATE SET
                           name=excluded.name, tier=excluded.tier, team=excluded.team, category=excluded.category,
                           location=excluded.location, gender=excluded.gender, exec_poc=excluded.exec_poc,
                           roster_followers=excluded.roster_followers, roster_avg_views=excluded.roster_avg_views,
                           roster_er=excluded.roster_er, raw_json=excluded.raw_json,
                           source_file=excluded.source_file, imported_at=excluded.imported_at,
                           youtube_raw=COALESCE(excluded.youtube_raw, roster_creator.youtube_raw)""",
                    (r.vendor_code, r.name, r.tier, r.team, r.category, r.location, r.gender, r.exec_poc,
                     r.roster_followers, r.roster_avg_views, r.roster_er, dumps(r.raw), Path(r.source_file).name, ts,
                     r.youtube_raw or None))
                creators += 1
            if r.vendor_code and r.youtube_ref:
                conn.execute("""INSERT OR IGNORE INTO roster_youtube (vendor_code, yt_ref, youtube_raw, linked_at)
                                VALUES (?,?,?,?)""", (r.vendor_code, r.youtube_ref, r.youtube_raw, ts))
            if r.handle:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO roster_link (vendor_code, username, source_file, linked_at) VALUES (?,?,?,?)",
                    (r.vendor_code, r.handle, Path(r.source_file).name, ts))
                links += cur.rowcount
                touch_handle(conn, r.handle, ts)
    return {"roster_creators_upserted": creators, "new_links": links}


def touch_handle(conn: sqlite3.Connection, username: str, ts: str | None = None) -> None:
    ts = ts or now()
    conn.execute("""INSERT INTO ig_handle (username, first_seen, last_seen) VALUES (?,?,?)
                    ON CONFLICT(username) DO UPDATE SET last_seen=excluded.last_seen""", (username, ts, ts))


# ----------------------------------------------------------------- accounts

def _merge_accounts(conn: sqlite3.Connection, keep: int, drop: int) -> None:
    """Fold account `drop` into `keep` (same Instagram account seen first via two id types)."""
    for table in ("profile_snapshot", "brand_evidence"):
        conn.execute(f"UPDATE OR IGNORE {table} SET account_id=? WHERE account_id=?", (keep, drop))
        conn.execute(f"DELETE FROM {table} WHERE account_id=?", (drop,))
    for row in conn.execute("SELECT * FROM account_post WHERE account_id=?", (drop,)).fetchall():
        link_account_post(conn, keep, row["shortcode"], row["relation"], loads(row["surfaces_json"], []))
    conn.execute("DELETE FROM account_post WHERE account_id=?", (drop,))
    conn.execute("UPDATE ig_handle SET account_id=? WHERE account_id=?", (keep, drop))
    old = conn.execute("SELECT ig_pk, graph_id FROM ig_account WHERE account_id=?", (drop,)).fetchone()
    conn.execute("DELETE FROM ig_account WHERE account_id=?", (drop,))
    if old:
        conn.execute("UPDATE ig_account SET ig_pk=COALESCE(ig_pk, ?), graph_id=COALESCE(graph_id, ?) WHERE account_id=?",
                     (old["ig_pk"], old["graph_id"], keep))


def resolve_account(conn: sqlite3.Connection, username: str, ig_pk: str | None = None,
                    graph_id: str | None = None, ts: str | None = None) -> int:
    """Return the stable account_id for this observation, creating or linking as needed.

    Platform ids win over handles: if a handle now belongs to a different Instagram user
    (renamed or recycled), the handle is re-pointed and the old history stays with the old id.
    """
    ts = ts or now()
    username = username.lower()
    ig_pk = str(ig_pk) if ig_pk else None
    graph_id = str(graph_id) if graph_id else None
    by_pk = conn.execute("SELECT account_id FROM ig_account WHERE ig_pk=?", (ig_pk,)).fetchone() if ig_pk else None
    by_graph = (conn.execute("SELECT account_id FROM ig_account WHERE graph_id=?", (graph_id,)).fetchone()
                if graph_id else None)
    handle = conn.execute("SELECT account_id FROM ig_handle WHERE username=?", (username,)).fetchone()
    handle_acct = handle["account_id"] if handle else None

    account_id = None
    if by_pk and by_graph and by_pk[0] != by_graph[0]:
        _merge_accounts(conn, by_pk[0], by_graph[0])
        account_id = by_pk[0]
    elif by_pk:
        account_id = by_pk[0]
    elif by_graph:
        account_id = by_graph[0]
    elif handle_acct is not None:
        row = conn.execute("SELECT ig_pk, graph_id FROM ig_account WHERE account_id=?", (handle_acct,)).fetchone()
        conflict = (ig_pk and row["ig_pk"] and row["ig_pk"] != ig_pk) or \
                   (graph_id and row["graph_id"] and row["graph_id"] != graph_id)
        account_id = None if conflict else handle_acct

    if account_id is None:
        cur = conn.execute(
            "INSERT INTO ig_account (ig_pk, graph_id, current_username, first_seen, last_seen) VALUES (?,?,?,?,?)",
            (ig_pk, graph_id, username, ts, ts))
        account_id = cur.lastrowid
    else:
        conn.execute("""UPDATE ig_account SET current_username=?, last_seen=?,
                            ig_pk=COALESCE(ig_pk, ?), graph_id=COALESCE(graph_id, ?) WHERE account_id=?""",
                     (username, ts, ig_pk, graph_id, account_id))
    touch_handle(conn, username, ts)
    conn.execute("UPDATE ig_handle SET account_id=? WHERE username=?", (account_id, username))
    return account_id


def account_for_handle(conn: sqlite3.Connection, username: str) -> int | None:
    row = conn.execute("SELECT account_id FROM ig_handle WHERE username=?", (username.lower(),)).fetchone()
    return row["account_id"] if row else None


# ----------------------------------------------------------------- observations

def add_profile_snapshot(conn, account_id: int, username: str, source: str, run_id: int | None,
                         profile: dict, observed_at: str) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO profile_snapshot (account_id, username, observed_at, source, run_id, followers,
               following, media_count, full_name, biography, website, bio_links_json, is_verified, is_private,
               profile_pic_url) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (account_id, username, observed_at, source, run_id, profile.get("followers"), profile.get("following"),
         profile.get("media_count"), profile.get("full_name"), profile.get("biography"), profile.get("website"),
         dumps(profile.get("bio_links")), _b(profile.get("is_verified")), _b(profile.get("is_private")),
         profile.get("profile_pic_url")))


def _b(v):
    return None if v is None else int(bool(v))


POST_FIELDS = ("media_pk", "graph_media_id", "owner_username", "owner_pk", "taken_at", "approx_date", "format", "product_type",
               "caption", "hashtags", "mentions", "coauthors", "usertags", "location_name", "audio",
               "accessibility_caption", "permalink", "metrics_hidden")
JSON_FIELDS = {"hashtags", "mentions", "coauthors", "usertags", "audio"}


def upsert_post(conn, post: dict, observed_at: str, detail_source: str | None = None) -> None:
    """Insert or enrich a post. A field missing from this observation keeps its stored value."""
    code = post["shortcode"]
    existing = conn.execute("SELECT * FROM post WHERE shortcode=?", (code,)).fetchone()
    values = {}
    for f in POST_FIELDS:
        col = f + "_json" if f in JSON_FIELDS else f
        v = post.get(f)
        if f in JSON_FIELDS:
            v = dumps(v)
        elif f == "metrics_hidden":
            v = _b(v)
        values[col] = v
    if existing is None:
        cols = ["shortcode", *values, "detail_source", "detail_fetched_at", "first_seen", "last_seen"]
        params = [code, *values.values(), detail_source, observed_at if detail_source else None,
                  observed_at, observed_at]
        conn.execute(f"INSERT INTO post ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", params)
        return
    sets, params = [], []
    for col, v in values.items():
        if v is not None:
            sets.append(f"{col}=?")
            params.append(v)
    sets.append("last_seen=?")
    params.append(observed_at)
    if detail_source:
        sets += ["detail_source=?", "detail_fetched_at=?"]
        params += [detail_source, observed_at]
    conn.execute(f"UPDATE post SET {', '.join(sets)} WHERE shortcode=?", [*params, code])


def link_account_post(conn, account_id: int, shortcode: str, relation: str, surfaces, ts: str | None = None) -> None:
    ts = ts or now()
    surfaces = [surfaces] if isinstance(surfaces, str) else list(surfaces)
    row = conn.execute("SELECT relation, surfaces_json FROM account_post WHERE account_id=? AND shortcode=?",
                       (account_id, shortcode)).fetchone()
    if row is None:
        conn.execute("""INSERT INTO account_post (account_id, shortcode, relation, surfaces_json, first_seen, last_seen)
                        VALUES (?,?,?,?,?,?)""", (account_id, shortcode, relation, dumps(sorted(set(surfaces))), ts, ts))
        return
    best = max(row["relation"], relation, key=lambda r: RELATION_RANK.get(r, 0))
    merged = sorted(set(loads(row["surfaces_json"], [])) | set(surfaces))
    conn.execute("UPDATE account_post SET relation=?, surfaces_json=?, last_seen=? WHERE account_id=? AND shortcode=?",
                 (best, dumps(merged), ts, account_id, shortcode))


def add_metric(conn, shortcode: str, source: str, run_id: int | None, observed_at: str, *,
               likes=None, comments=None, views=None, plays=None, shares=None, reposts=None) -> None:
    if all(v is None for v in (likes, comments, views, plays, shares, reposts)):
        return
    conn.execute("""INSERT OR REPLACE INTO post_metric (shortcode, observed_at, source, run_id, likes, comments, views,
                        plays, shares, reposts) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                 (shortcode, observed_at, source, run_id, likes, comments, views, plays, shares, reposts))


def log_request(conn, *, run_id, username, source, kind, target, outcome, http_status=None, ms=None) -> None:
    conn.execute("""INSERT INTO request_log (ts, run_id, username, source, kind, target, outcome, http_status, ms)
                    VALUES (?,?,?,?,?,?,?,?,?)""", (now(), run_id, username, source, kind, target, outcome, http_status, ms))


# ----------------------------------------------------------------- runs

def open_run(conn, mode: str, input_key: str, input_files: list[str], args: dict, resume: bool = True) -> tuple[int, bool]:
    """Return (run_id, resumed). An unfinished run with the same input is resumed by default."""
    if resume:
        row = conn.execute("""SELECT run_id FROM run WHERE finished_at IS NULL AND input_key=? AND mode=?
                              ORDER BY run_id DESC LIMIT 1""", (input_key, mode)).fetchone()
        if row:
            return row["run_id"], True
    with conn:
        cur = conn.execute("INSERT INTO run (started_at, mode, input_key, input_files, args_json) VALUES (?,?,?,?,?)",
                           (now(), mode, input_key, json.dumps(input_files), json.dumps(args, default=str)))
    return cur.lastrowid, False


def finished_accounts(conn, run_id: int) -> set[str]:
    rows = conn.execute("""SELECT username FROM run_account WHERE run_id=?
                           AND status NOT IN ('rate_limited','challenge','error')""", (run_id,)).fetchall()
    return {r["username"] for r in rows}


def save_run_account(conn, run_id: int, username: str, **fields) -> None:
    cols = ["run_id", "username", *fields]
    vals = [run_id, username, *[dumps(v) if isinstance(v, (dict, list)) else v for v in fields.values()]]
    conn.execute(f"INSERT OR REPLACE INTO run_account ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)


def close_run(conn, run_id: int, totals: dict) -> None:
    with conn:
        conn.execute("UPDATE run SET finished_at=?, totals_json=? WHERE run_id=?", (now(), json.dumps(totals), run_id))

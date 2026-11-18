"""Per-creator collection waterfall.

1. Meta Business Discovery (if configured): profile + latest posts with likes/comments/Reel views.
2. Logged-out profile page (full runs, or whenever Meta could not serve the account):
   bio links, verified flag, both platform ids, first 12 timeline entries (exposes collab
   posts owned by other accounts); timeline pagination until the window is covered.
3. Logged-out Reels tab when Reel views are missing (plays).
4. Logged-out post pages, selectively: posts with brand signals, collab posts, and every
   post of accounts Meta cannot serve. Each post page is read once and cached.
5. Window = latest N by publication time ∪ last D days; brand evidence re-derived for it.

Progress is committed after every step, so an interrupted run loses at most one request.
"""

from __future__ import annotations

import gzip
import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from . import db
from .brands import DISCLOSURE_RE, PROMO_RE, Registry, derive_edges
from .budget import Outcome, SourceStopped
from .config import Settings
from .metrics import select_window, window_satisfied
from .util import dumps, iso, loads, parse_iso, utcnow

MAX_TIMELINE_PAGES = 6


@dataclass
class AccountReport:
    username: str
    depth: str
    status: str = Outcome.ERROR.value
    coverage: dict = field(default_factory=dict)
    requests: Counter = field(default_factory=Counter)
    posts_in_window: int = 0
    elapsed_s: float = 0.0
    error: str = ""
    metrics_level: str | None = None   # none | web | meta (best likes/comments source this run)


def has_brand_signal(post: dict, registry: Registry) -> bool:
    caption = post.get("caption") or ""
    if DISCLOSURE_RE.search(caption) or PROMO_RE.search(caption):
        return True
    if post.get("mentions"):
        return True
    return any(registry.brand_for_hashtag(t) for t in post.get("hashtags") or [])


class RawStore:
    def __init__(self, root: Path | None):
        self.root = root

    def save(self, day: str, username: str, name: str, payload) -> None:
        if self.root is None or payload is None:
            return
        d = self.root / day / username
        d.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
        with gzip.open(d / f"{safe}.json.gz", "wt", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, default=str)


class Collector:
    def __init__(self, conn, settings: Settings, run_id: int, *, meta=None, web=None,
                 registry: Registry | None = None, roster_handles: set[str] | None = None,
                 detail_mode: str = "signals", raw: RawStore | None = None,
                 now_fn: Callable[[], datetime] = utcnow, log: Callable[[str], None] = print):
        self.conn = conn
        self.s = settings
        self.run_id = run_id
        self.meta = meta
        self.web = web
        self.registry = registry or Registry()
        self.roster_handles = roster_handles or set()
        self.detail_mode = detail_mode
        self.raw = raw or RawStore(None)
        self.now_fn = now_fn
        self.log = log
        self._report: AccountReport | None = None

    # ------------------------------------------------------------ recording

    def recorder(self, source, kind, target, outcome, http_status, ms) -> None:
        username = self._report.username if self._report is not None else target
        if self._report is not None:
            self._report.requests[f"{source}:{kind}"] += 1
        db.log_request(self.conn, run_id=self.run_id, username=username, source=source, kind=kind, target=target,
                       outcome=outcome, http_status=http_status, ms=ms)

    # ------------------------------------------------------------ helpers

    def _account_posts(self, account_id: int) -> list[dict]:
        rows = self.conn.execute(
            """SELECT p.*, ap.relation, ap.surfaces_json FROM account_post ap JOIN post p USING (shortcode)
               WHERE ap.account_id=?""", (account_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            for f in ("hashtags", "mentions", "coauthors", "usertags", "audio"):
                d[f] = loads(d.pop(f + "_json"), None)
            d["surfaces"] = loads(d.pop("surfaces_json"), [])
            d["sort_date"] = d.get("taken_at") or d.get("approx_date")
            out.append(d)
        return out

    def _window(self, account_id: int, now: datetime) -> list[dict]:
        posts = self._account_posts(account_id)
        for p in posts:
            p["taken_at_for_window"] = p["sort_date"]
        keep = select_window([{"shortcode": p["shortcode"], "taken_at": p["taken_at_for_window"]} for p in posts],
                             now, self.s.window_posts, self.s.window_days)
        by = {p["shortcode"]: p for p in posts}
        return [by[c] for c in keep]

    def _link_from_web(self, account_id: int, username: str, post: dict, surface: str, ts: str) -> None:
        owner = post.get("owner_username")
        coauthors = post.get("coauthors") or []
        if owner == username:
            rel = "owner"
        elif username in coauthors:
            rel = "coauthor"
        else:
            rel = "profile_association"
        db.link_account_post(self.conn, account_id, post["shortcode"], rel, [surface], ts)

    # ------------------------------------------------------------ steps

    def _meta_step(self, rep: AccountReport, username: str, now: datetime, ts: str, day: str):
        if self.meta is None:
            rep.coverage["meta_bd"] = "not_configured"
            return None, False
        try:
            res = self.meta.fetch(username, now, self.s.window_posts, self.s.window_days)
        except SourceStopped as exc:
            rep.coverage["meta_bd"] = f"stopped: {exc.reason}"
            return None, False
        if res.outcome != Outcome.OK.value:
            rep.coverage["meta_bd"] = res.outcome
            if res.outcome == Outcome.UNAVAILABLE.value:
                self.conn.execute("UPDATE ig_handle SET meta_available=0 WHERE username=?", (username,))
            self.conn.commit()
            return None, False
        for i, page in enumerate(res.raw_pages):
            self.raw.save(day, username, f"meta_bd_{i}", page)
        prof = res.profile
        account_id = db.resolve_account(self.conn, username, graph_id=prof.get("graph_id"), ts=ts)
        db.add_profile_snapshot(self.conn, account_id, username, "meta_bd", self.run_id, prof, ts)
        for p in res.posts:
            db.upsert_post(self.conn, {**p, "owner_username": username}, ts)
            db.link_account_post(self.conn, account_id, p["shortcode"], "owner", ["meta_bd"], ts)
            db.add_metric(self.conn, p["shortcode"], "meta_bd", self.run_id, ts,
                          likes=p["likes"], comments=p["comments"], views=p["views"],
                          shares=p.get("shares"), reposts=p.get("reposts"))
        self.conn.execute("UPDATE ig_handle SET meta_available=1 WHERE username=?", (username,))
        self.conn.commit()
        rep.coverage["meta_bd"] = {"posts": len(res.posts), "pages": res.pages, "reached_end": res.exhausted,
                                   **({"note": res.detail} if res.detail else {})}
        return account_id, True

    def _web_profile_step(self, rep, username, account_id, ts, day, paginate: bool, now: datetime):
        try:
            res = self.web.profile_page(username)
        except SourceStopped as exc:
            rep.coverage["web_profile"] = f"stopped: {exc.reason}"
            return account_id, None
        if res.outcome in (Outcome.OK.value, Outcome.PRIVATE.value):
            prof = res.data
            self.raw.save(day, username, "web_profile", res.extra.get("raw"))
            account_id = db.resolve_account(self.conn, username, ig_pk=prof.get("ig_pk"),
                                            graph_id=prof.get("graph_id"), ts=ts)
            db.add_profile_snapshot(self.conn, account_id, username, "web_profile", self.run_id, prof, ts)
            self.conn.commit()
            if res.outcome == Outcome.PRIVATE.value:
                rep.coverage["web_profile"] = "private"
                return account_id, "private"
        else:
            rep.coverage["web_profile"] = res.outcome
            if res.detail:
                rep.error = res.detail
            self.raw.save(day, username, "web_profile_failed", res.extra.get("raw"))
            return account_id, res.outcome
        rep.coverage["web_profile"] = "ok"

        from .parse.web import timeline_nodes
        nodes, info = timeline_nodes(prof.get("timeline"))
        dates: list[str | None] = []
        pages = 1
        known = {r["shortcode"]: r["taken_at"] for r in self.conn.execute(
            "SELECT shortcode, taken_at FROM post WHERE owner_username=?", (username,))}

        def absorb(batch):
            for n in batch:
                db.upsert_post(self.conn, {k: n[k] for k in ("shortcode", "media_pk", "owner_username", "format",
                                                             "product_type", "caption", "hashtags", "mentions",
                                                             "accessibility_caption")}
                               | {"approx_date": n.get("approx_date")}, ts)
                self._link_from_web(account_id, username, n, "web_timeline", ts)
                dates.append(known.get(n["shortcode"]) or n.get("approx_date"))

        absorb(nodes)
        self.conn.commit()
        stop_reason = None
        while paginate and info.get("has_next_page") and info.get("end_cursor") and pages < MAX_TIMELINE_PAGES:
            if window_satisfied(dates, now, self.s.window_posts, self.s.window_days):
                break
            page_id = prof.get("graph_id")
            if not page_id:
                stop_reason = "no page id"
                break
            try:
                r = self.web.timeline_page(username, page_id, info["end_cursor"])
            except SourceStopped as exc:
                stop_reason = f"stopped: {exc.reason}"
                break
            if r.outcome != Outcome.OK.value:
                stop_reason = r.outcome
                break
            pages += 1
            self.raw.save(day, username, f"web_timeline_{pages}", r.extra.get("raw"))
            absorb(r.data)
            info = r.extra.get("page_info") or {}
            self.conn.commit()
        reached_end = not info.get("has_next_page")
        satisfied = window_satisfied(dates, now, self.s.window_posts, self.s.window_days) or reached_end
        rep.coverage["web_timeline"] = {"entries": len(dates), "pages": pages, "reached_end": reached_end,
                                        "window_covered": satisfied, **({"stopped": stop_reason} if stop_reason else {})}
        return account_id, "ok"

    def _reels_step(self, rep, username, account_id, ts, day):
        try:
            res = self.web.reels_page(username)
        except SourceStopped as exc:
            rep.coverage["web_reels"] = f"stopped: {exc.reason}"
            return
        if res.outcome != Outcome.OK.value:
            rep.coverage["web_reels"] = res.outcome
            return
        self.raw.save(day, username, "web_reels", res.extra.get("raw"))
        acct = self.conn.execute("SELECT graph_id FROM ig_account WHERE account_id=?", (account_id,)).fetchone()
        mine = acct["graph_id"] if acct else None
        for n in res.data:
            db.upsert_post(self.conn, {"shortcode": n["shortcode"], "media_pk": n["media_pk"], "format": "reel",
                                       "metrics_hidden": n["metrics_hidden"]}, ts)
            rel = "owner" if mine and n.get("owner_graph_id") == mine else "profile_association"
            db.link_account_post(self.conn, account_id, n["shortcode"], rel, ["web_reels"], ts)
            db.add_metric(self.conn, n["shortcode"], "web_reels", self.run_id, ts,
                          likes=n["likes"], comments=n["comments"], plays=n["plays"])
        self.conn.commit()
        more = (res.extra.get("page_info") or {}).get("has_next_page")
        rep.coverage["web_reels"] = {"entries": len(res.data), "first_page_only": bool(more)}

    def _detail_step(self, rep, username, account_id, meta_ok, depth, now, ts, day):
        if self.web is None or self.detail_mode == "none":
            rep.coverage["web_post"] = "skipped"
            return
        window = self._window(account_id, now)
        recent_cutoff = now - timedelta(days=self.s.window_days)
        todo = []
        for p in window:
            fetched = p.get("detail_fetched_at")
            if fetched:
                # Without Meta, counts only come from post pages: refresh young posts on full runs.
                young = p.get("sort_date") and parse_iso(p["sort_date"]) >= recent_cutoff
                if meta_ok or depth != "full" or not young or (parse_iso(fetched) > now - timedelta(days=5)):
                    continue
            elif self.detail_mode == "signals" and meta_ok:
                if p.get("owner_username") in (None, username) and "meta_bd" in p["surfaces"] \
                        and not has_brand_signal(p, self.registry):
                    continue
            todo.append(p["shortcode"])
        done = failed = 0
        stop = None
        for code in todo:
            try:
                r = self.web.post_page(username, code)
            except SourceStopped as exc:
                stop = f"stopped: {exc.reason}"
                break
            if r.outcome == Outcome.OK.value:
                post, m = r.data
                self.raw.save(day, username, f"post_{code}", r.extra.get("raw"))
                db.upsert_post(self.conn, post, ts, detail_source="web_post")
                self._link_from_web(account_id, username, post, "web_post", ts)
                db.add_metric(self.conn, code, "web_post", self.run_id, ts, likes=m["likes"], comments=m["comments"],
                              plays=m["plays"])
                self.conn.commit()
                done += 1
            elif r.outcome in (Outcome.RATE_LIMITED.value, Outcome.LOGIN_WALL.value, Outcome.CHALLENGE.value):
                stop = r.outcome
                break
            else:
                failed += 1
        rep.coverage["web_post"] = {"needed": len(todo), "fetched": done, "failed": failed,
                                    **({"stopped": stop} if stop else {})}

    def _evidence_step(self, account_id, username, now, ts) -> list[dict]:
        window = self._window(account_id, now)
        for p in window:
            for e in derive_edges(p, username, self.registry, self.roster_handles):
                kind = ("creator" if e.brand_handle in self.roster_handles else
                        "brand" if e.brand_handle in self.registry.brands else "unknown")
                if not e.brand_handle.startswith("("):
                    info = self.registry.brands.get(e.brand_handle, {})
                    self.conn.execute(
                        """INSERT INTO brand (handle, name, category, kind, source, first_seen) VALUES (?,?,?,?,?,?)
                           ON CONFLICT(handle) DO UPDATE SET kind=excluded.kind,
                               name=COALESCE(excluded.name, brand.name), category=COALESCE(excluded.category, brand.category),
                               source=CASE WHEN excluded.source='registry' THEN 'registry' ELSE brand.source END""",
                        (e.brand_handle, info.get("name"), info.get("category"), kind,
                         "registry" if info else f"observed_{e.rule_id}", ts))
                self.conn.execute(
                    """INSERT INTO brand_evidence (shortcode, account_id, brand_handle, kind, evidence_text, source,
                           rule_id, review_status, first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(shortcode, account_id, brand_handle, kind) DO UPDATE SET
                           evidence_text=excluded.evidence_text, rule_id=excluded.rule_id, last_seen=excluded.last_seen,
                           review_status=CASE WHEN brand_evidence.review_status IN ('confirmed','rejected')
                               THEN brand_evidence.review_status ELSE excluded.review_status END""",
                    (p["shortcode"], account_id, e.brand_handle, e.kind, e.evidence_text,
                     p.get("detail_source") or "caption", e.rule_id, e.review_status, ts, ts))
        self.conn.commit()
        return window

    # ------------------------------------------------------------ main

    def collect(self, username: str, depth: str = "full") -> AccountReport:
        rep = AccountReport(username=username, depth=depth)
        self._report = rep
        t0 = time.monotonic()
        now = self.now_fn()
        ts, day = iso(now), now.date().isoformat()
        db.touch_handle(self.conn, username, ts)
        try:
            account_id, meta_ok = self._meta_step(rep, username, now, ts, day)
            web_state = None
            need_web = self.web is not None and (depth == "full" or not meta_ok)
            if need_web:
                account_id, web_state = self._web_profile_step(
                    rep, username, account_id, ts, day, paginate=(depth == "full"), now=now)
            elif self.web is None:
                rep.coverage["web_profile"] = "not_configured"
            if account_id is None:
                rep.status = self._status_without_data(rep, web_state)
                return rep
            if web_state == "private":
                rep.status = Outcome.PRIVATE.value
                return rep
            if self.web is not None and web_state == "ok" and depth == "full":
                window = self._window(account_id, now)
                reels = [p for p in window if p.get("format") == "reel"]
                missing_views = [p for p in reels if not self._has_metric(p["shortcode"], "views")]
                if not meta_ok or missing_views:
                    self._reels_step(rep, username, account_id, ts, day)
            if self.web is not None and (web_state == "ok" or (meta_ok and depth == "full")):
                self._detail_step(rep, username, account_id, meta_ok, depth, now, ts, day)
            window = self._evidence_step(account_id, username, now, ts)
            rep.posts_in_window = len(window)
            if meta_ok:
                rep.metrics_level = "meta"
            elif window and sum(self._has_metric(p["shortcode"], "likes") for p in window) * 2 >= len(window):
                rep.metrics_level = "web"   # counts for at least half the window (not just a few Reels-tab rows)
            else:
                rep.metrics_level = "none"
                rep.coverage["metrics"] = "none: no likes/comments (no Meta and --detail none)"
            rep.status = self._final_status(rep, meta_ok, web_state, window)
            return rep
        except KeyboardInterrupt:
            rep.status, rep.error = Outcome.ERROR.value, "interrupted"
            raise
        except Exception as exc:  # noqa: BLE001 - one account must not stop the batch
            self.conn.rollback()
            rep.status, rep.error = Outcome.ERROR.value, f"{type(exc).__name__}: {exc}"[:500]
            return rep
        finally:
            rep.elapsed_s = round(time.monotonic() - t0, 2)
            self._finish(rep, ts)
            self._report = None

    def _has_metric(self, code: str, col: str) -> bool:
        return self.conn.execute(f"SELECT 1 FROM post_metric WHERE shortcode=? AND {col} IS NOT NULL LIMIT 1",
                                 (code,)).fetchone() is not None

    @staticmethod
    def _status_without_data(rep: AccountReport, web_state) -> str:
        meta = rep.coverage.get("meta_bd")
        states = [web_state, meta if isinstance(meta, str) else None]
        for wanted in (Outcome.NOT_FOUND.value, Outcome.CHALLENGE.value, Outcome.LOGIN_WALL.value,
                       Outcome.RATE_LIMITED.value):
            if wanted in states:
                return Outcome.CHALLENGE.value if wanted == Outcome.LOGIN_WALL.value else wanted
        if meta == Outcome.UNAVAILABLE.value and web_state is None:
            rep.error = "Meta: not a professional account, age-gated or not found; logged-out web is disabled"
            return Outcome.ERROR.value
        if any(isinstance(v, str) and v.startswith("stopped") for v in rep.coverage.values()):
            return Outcome.RATE_LIMITED.value
        return Outcome.ERROR.value

    def _final_status(self, rep: AccountReport, meta_ok: bool, web_state, window: list[dict]) -> str:
        problems = []
        for key, v in rep.coverage.items():
            if isinstance(v, str) and v not in ("ok", "not_configured", "skipped", Outcome.UNAVAILABLE.value):
                problems.append(key)
            if isinstance(v, dict) and (v.get("stopped") or v.get("window_covered") is False):
                problems.append(key)
            if isinstance(v, dict) and key == "web_post" and v.get("fetched", 0) < v.get("needed", 0):
                problems.append(key)
        if not meta_ok and web_state != "ok":
            return Outcome.PARTIAL.value if window else Outcome.ERROR.value
        undated = [p for p in window if not p.get("taken_at")]
        if undated:
            rep.coverage["undated_posts_in_window"] = len(undated)
            problems.append("undated")
        return "partial" if problems else Outcome.OK.value

    def _finish(self, rep: AccountReport, ts: str) -> None:
        try:
            db.save_run_account(self.conn, self.run_id, rep.username, depth=rep.depth, status=rep.status,
                                coverage_json=rep.coverage, requests_json=dict(rep.requests),
                                posts_in_window=rep.posts_in_window, elapsed_s=rep.elapsed_s, error=rep.error,
                                started_at=ts, finished_at=db.now())
            col = {"full": "last_full_at", "light": "last_light_at"}.get(rep.depth)
            # A partial result caused by a refusal (rate limit, login wall, stopped source) stays due,
            # so the next --due run retries it. Other partial results (e.g. undated posts) do not.
            blob = json.dumps(rep.coverage)
            retryable = any(m in blob for m in ("stopped", "rate_limited", "login_wall", "challenge", "auth_error"))
            if col and (rep.status in ("ok", "private") or (rep.status == "partial" and not retryable)):
                self.conn.execute(f"UPDATE ig_handle SET {col}=?, last_status=? WHERE username=?",
                                  (ts, rep.status, rep.username))
                if rep.depth == "full" and rep.metrics_level:
                    self.conn.execute("UPDATE ig_handle SET metrics_level=? WHERE username=?",
                                      (rep.metrics_level, rep.username))
            else:
                self.conn.execute("UPDATE ig_handle SET last_status=? WHERE username=?", (rep.status, rep.username))
            self.conn.commit()
        except Exception:  # noqa: BLE001
            self.conn.rollback()

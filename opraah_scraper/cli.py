"""Command line: python -m opraah_scraper <command> ...

  roster      import roster CSV/TXT files, print counts, write data/exports/roster_review.csv
  run         import + collect + export (resumable; --due for weekly/monthly refresh)
  check-meta  one Business Discovery call to confirm the Meta token works
  export      regenerate CSVs and the wiki from the database
  status      last run summary, request counts and throughput estimate
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path

from . import db
from .brands import Registry
from .budget import Outcome, Pacer
from .collect import Collector, RawStore
from .config import Settings, load_settings
from .export import export_all
from .roster import load_inputs, write_review_csv
from .util import parse_iso, utcnow


def _settings(args) -> Settings:
    s = load_settings(Path(args.env) if getattr(args, "env", None) else None)
    if getattr(args, "data_dir", None):
        s.data_dir = Path(args.data_dir).expanduser()
    return s


def _print_summary(imp) -> None:
    s = imp.summary()
    print(f"Roster rows: {s['rows']} {s['by_tier']}")
    print(f"  with Instagram handle: {s['rows_with_handle']}  without: {s['rows_without_handle']}  "
          f"invalid: {s['rows_with_invalid_handle']}")
    print(f"  distinct handles to fetch: {s['distinct_handles']}")
    print(f"  handles shared by >1 vendor code: {s['handles_shared_by_vendor_codes']}  "
          f"in both tiers: {s['handles_in_both_tiers']}  duplicate vendor codes: {s['duplicate_vendor_codes']}")


def cmd_roster(args) -> int:
    s = _settings(args)
    imp = load_inputs([Path(p) for p in args.input])
    _print_summary(imp)
    out = Path(args.review_out) if args.review_out else s.data_dir / "exports" / "roster_review.csv"
    n = write_review_csv(imp, out)
    print(f"Rows needing review: {n} -> {out}")
    if not args.dry_run:
        conn = db.connect(s.db_path)
        print(db.import_roster(conn, imp))
    return 0


LEVEL_RANK = {None: -1, "none": 0, "web": 1, "meta": 2}


def capability(s: Settings, args) -> str:
    """Best metrics source this run can use: meta > web (post pages) > none."""
    if s.meta_configured and not getattr(args, "no_meta", False):
        return "meta"
    if not getattr(args, "no_web", False) and (getattr(args, "detail", None) or "signals") != "none":
        return "web"
    return "none"


def _depth_for(conn, username: str, s: Settings, due: bool, forced: str | None, now, cap: str = "meta") -> str:
    if not due:
        return forced or "full"
    row = conn.execute("SELECT last_full_at, last_light_at, metrics_level FROM ig_handle WHERE username=?",
                       (username,)).fetchone()
    # Collected earlier without likes/comments (e.g. --detail none, no Meta) and this run can do
    # better: due now, instead of waiting 30 days.
    if row and row["last_full_at"] and LEVEL_RANK.get(row["metrics_level"], -1) < LEVEL_RANK[cap]:
        return "full"
    last_full = parse_iso(row["last_full_at"]) if row else None
    last_light = parse_iso(row["last_light_at"]) if row else None
    if last_full is None or now - last_full >= timedelta(days=s.full_every_days):
        return "full"
    last_any = max(d for d in (last_full, last_light) if d)
    if now - last_any >= timedelta(days=s.light_every_days):
        return "light"
    return "none"


def build_clients(conn, s: Settings, args, recorder_target):
    meta = web = None
    if s.meta_configured and not args.no_meta:
        from .sources.meta_bd import MetaBusinessDiscovery
        meta_pacer = Pacer("meta", 3600 / max(s.meta_hourly_cap, 1) * 0.5, 3600 / max(s.meta_hourly_cap, 1),
                           s.meta_hourly_cap, s.backoff_minutes, wait_on_limit=not args.no_wait_on_limit)
        meta = MetaBusinessDiscovery(s.meta_access_token, s.meta_ig_user_id, s.graph_version, meta_pacer,
                                     recorder=recorder_target)
    elif not args.no_meta:
        print("Meta Business Discovery is not configured (.env META_ACCESS_TOKEN / META_IG_USER_ID); "
              "using logged-out pages only. See docs/META_SETUP.md.")
    if not args.no_web:
        from .sources.web import WebClient
        web_pacer = Pacer("web", s.web_min_interval, s.web_max_interval, s.web_hourly_cap, s.backoff_minutes,
                          wait_on_limit=not args.no_wait_on_limit)
        web_pacer.burst_cap = (75, 660)   # Instaloader's documented window, now visible in the log
        web = WebClient(web_pacer, recorder=recorder_target, fetcher=s.web_fetcher,
                        obscura_path=s.obscura_path, obscura_sha256=s.obscura_sha256)
    return meta, web


class RunLock:
    """One collecting process per data directory: two would double-fetch and fight over the DB."""

    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        import os
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                pid = int(self.path.read_text().strip() or 0)
                os.kill(pid, 0)
                raise SystemExit(f"Another run (pid {pid}) is using {self.path.parent}. Wait for it, or stop it first.")
            except (ValueError, ProcessLookupError, PermissionError):
                pass  # stale lock from a crashed run
        self.path.write_text(str(os.getpid()))
        return self

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        return False


def cmd_run(args) -> int:
    s = _settings(args)
    with RunLock(s.data_dir / ".run.lock"):
        return _cmd_run(args, s)


def _cmd_run(args, s: Settings) -> int:
    if args.detail:
        detail = args.detail
    else:
        detail = "signals"
    conn = db.connect(s.db_path)
    imp = load_inputs([Path(p) for p in args.input])
    _print_summary(imp)
    db.import_roster(conn, imp)
    write_review_csv(imp, s.data_dir / "exports" / "roster_review.csv")
    handles = imp.handles
    if args.only:
        wanted = {h.strip().lstrip("@").lower() for h in args.only.split(",")}
        handles = [h for h in handles if h in wanted]
    now = utcnow()
    cap = capability(s, args)
    if cap == "none" and not args.no_web:
        print("WARNING: no Meta token and --detail none: this run collects captions, dates (day precision) and "
              "Reels-tab plays only, with NO likes/comments/engagement. Those accounts become due again "
              "automatically once Meta is configured or --detail signals is used.")
    plan = [(h, _depth_for(conn, h, s, args.due, args.depth, now, cap)) for h in handles]
    skipped = [h for h, d in plan if d == "none"]
    plan = [(h, d) for h, d in plan if d != "none"]
    if args.limit:
        plan = plan[:args.limit]
    key_src = json.dumps({"handles": [h for h, _ in plan], "mode": "due" if args.due else "run"}, sort_keys=True)
    input_key = hashlib.sha256(key_src.encode()).hexdigest()[:16]
    run_id, resumed = db.open_run(conn, "due" if args.due else "run", input_key, list(args.input),
                                  {"detail": detail, "limit": args.limit, "depth": args.depth}, resume=not args.new_run)
    done = db.finished_accounts(conn, run_id) if resumed else set()
    if resumed:
        print(f"Resuming run {run_id}: {len(done)} account(s) already done.")
    for h in skipped:
        db.save_run_account(conn, run_id, h, depth="none", status=Outcome.SKIPPED.value)
    conn.commit()

    collector = Collector(conn, s, run_id, registry=Registry.load(),
                          roster_handles={r["username"] for r in conn.execute("SELECT DISTINCT username FROM roster_link")},
                          detail_mode=detail, raw=RawStore(None if args.no_raw else s.data_dir / "raw"))
    meta, web = build_clients(conn, s, args, collector.recorder)
    collector.meta, collector.web = meta, web
    if meta is None and web is None:
        print("Nothing to collect with: Meta is not configured and --no-web was given.")
        return 2

    todo = [(h, d) for h, d in plan if h not in done]
    print(f"Run {run_id}: {len(todo)} account(s) to collect ({len(skipped)} not due). Detail pages: {detail}.")
    statuses = Counter()
    try:
        for i, (h, depth) in enumerate(todo, 1):
            rep = collector.collect(h, depth)
            statuses[rep.status] += 1
            req = dict(rep.requests)
            m = sum(v for k, v in req.items() if k.startswith("meta:"))
            w = sum(v for k, v in req.items() if k.startswith("web:"))
            print(f"[{i}/{len(todo)}] @{h} ({depth}) -> {rep.status}: {rep.posts_in_window} posts, "
                  f"meta {m} / web {w} requests, {rep.elapsed_s:.1f}s" + (f"  {rep.error}" if rep.error else ""))
    except KeyboardInterrupt:
        conn.commit()
        print("\nStopped. Progress is saved; run the same command again to resume.")
        return 130
    db.close_run(conn, run_id, dict(statuses))
    print("Statuses:", dict(statuses))
    _export(conn, s)
    return 0


def _export(conn, s: Settings, sync_dir: Path | None = None) -> None:
    from . import kb_export
    now = utcnow()
    out = s.data_dir / "exports"
    # data/exports/creators.csv keeps its columns: ~/Downloads/opraah_data/cleanup/clean_roster.py reads it.
    counts = export_all(conn, out, now, s.window_posts, s.window_days)
    res = kb_export.build(conn, s.data_dir / "kb", now, s.window_posts, s.window_days)
    yt_rows = []
    for p in res["profiles"]:
        for v in kb_export._video_rows(p.get("youtube") or {}):
            yt_rows.append({"vendor_code": p["vendor_code"], "channel": (p.get("youtube") or {}).get("handle"), **v})
    kb_export._write_csv(out / "youtube_videos.csv", ["vendor_code", "channel", *kb_export.VIDEO_COLS], yt_rows)
    counts["youtube_videos.csv"] = len(yt_rows)
    print("Exports:", ", ".join(f"{k} ({v} rows)" for k, v in counts.items()), "->", out)
    print(f"Knowledge base: {res['creators']} creator folders, {res['brands']} brands -> {s.data_dir / 'kb'}")
    target = sync_dir or s.kb_sync_dir
    if target:
        r = kb_export.sync_to(s.data_dir / "kb", Path(target).expanduser())
        print(f"Synced knowledge base to {target}: {r['copied']} file(s) updated, {r['removed']} removed")


def cmd_export(args) -> int:
    s = _settings(args)
    _export(db.connect(s.db_path), s, Path(args.to).expanduser() if args.to else None)
    return 0


def cmd_youtube(args) -> int:
    s = _settings(args)
    if not s.youtube_api_key:
        print("YOUTUBE_API_KEY is not set. See docs/YOUTUBE_SETUP.md.")
        return 2
    from .collect_youtube import collect_refs, units_used_today
    from .sources.youtube import YouTubeClient
    with RunLock(s.data_dir / ".youtube.lock"):
        conn = db.connect(s.db_path)
        imp = load_inputs([Path(p) for p in args.input])
        db.import_roster(conn, imp)
        refs = list(dict.fromkeys(r.youtube_ref for r in imp.rows if r.youtube_ref))
        if args.only:
            wanted = {x.strip().lower() for x in args.only.split(",")}
            refs = [r for r in refs if r in wanted or r.lstrip("@") in wanted]
        if args.limit:
            refs = refs[:args.limit]
        legacy = sum(1 for r in refs if r.startswith("c:"))
        print(f"YouTube: {len(refs)} channel link(s) in the input ({legacy} legacy /c/ links"
              f"{' resolved by search, 100 units each' if args.allow_search else ' skipped without --allow-search'}). "
              f"Units used today: {units_used_today(conn, utcnow())}/{s.youtube_daily_units}.")
        run_id, _ = db.open_run(conn, "youtube", "yt", list(args.input), {"limit": args.limit}, resume=False)

        def rec(channel, endpoint, units, outcome, status):
            conn.execute("INSERT INTO yt_request_log (ts, run_id, channel, endpoint, units, outcome, http_status) "
                         "VALUES (?,?,?,?,?,?,?)", (db.now(), run_id, channel, endpoint, units, outcome, status))

        client = YouTubeClient(s.youtube_api_key, Pacer("youtube", 0.2, 0.6, 3000),
                               daily_units=s.youtube_daily_units,
                               units_used_today=lambda: units_used_today(conn, utcnow()),
                               recorder=rec, allow_search=args.allow_search)
        reports = collect_refs(conn, client, refs, run_id, utcnow, n=s.window_posts, days=s.window_days,
                               due_days=s.light_every_days if args.due else None)
        st = Counter(r.status for r in reports)
        db.close_run(conn, run_id, dict(st))
        print("YouTube statuses:", dict(st), f"units this run: {client.units_this_run}")
        _export(conn, s)
    return 0


def cmd_check_meta(args) -> int:
    s = _settings(args)
    if not s.meta_configured:
        print("META_ACCESS_TOKEN and META_IG_USER_ID are not set. See docs/META_SETUP.md.")
        return 2
    from .sources.meta_bd import MetaBusinessDiscovery
    calls = []
    pacer = Pacer("meta", 0, 0, 0)
    client = MetaBusinessDiscovery(s.meta_access_token, s.meta_ig_user_id, s.graph_version, pacer,
                                   recorder=lambda *a: calls.append(a), max_pages=1)
    res = client.fetch(args.username.lower(), utcnow())
    if res.outcome != Outcome.OK.value:
        print(f"Business Discovery failed for @{args.username}: {res.outcome} {res.detail}")
        return 1
    p = res.profile
    print(f"OK: @{p['username']} followers={p['followers']} media={p['media_count']} posts returned={len(res.posts)}")
    for post in res.posts[:5]:
        print(f"  {post['taken_at']} {post['format']:<8} likes={post['likes']} comments={post['comments']} "
              f"views={post['views']} {post['permalink']}")
    return 0


def cmd_status(args) -> int:
    s = _settings(args)
    conn = db.connect(s.db_path)
    run = conn.execute("SELECT * FROM run ORDER BY run_id DESC LIMIT 1").fetchone()
    if not run:
        print("No runs yet.")
        return 0
    rows = conn.execute("SELECT * FROM run_account WHERE run_id=? AND status<>'skipped_not_due'", (run["run_id"],)).fetchall()
    st = Counter(r["status"] for r in rows)
    req = Counter()
    for r in rows:
        req.update(json.loads(r["requests_json"] or "{}"))
    n = len(rows) or 1
    elapsed = sum(r["elapsed_s"] or 0 for r in rows)
    print(f"Run {run['run_id']} ({run['mode']}) started {run['started_at']}, finished {run['finished_at'] or '— (resumable)'}")
    print("Statuses:", dict(st))
    print("Requests:", dict(req))
    meta = sum(v for k, v in req.items() if k.startswith("meta:"))
    web = sum(v for k, v in req.items() if k.startswith("web:"))
    print(f"Per account: {meta / n:.1f} Meta + {web / n:.1f} web requests, {elapsed / n:.1f}s")
    total = conn.execute("SELECT COUNT(DISTINCT username) FROM roster_link").fetchone()[0]
    if rows and total:
        hours = elapsed / n * total / 3600
        print(f"Extrapolated to {total} roster handles at this pace: ~{hours:.1f} h, "
              f"~{meta / n * total:.0f} Meta and ~{web / n * total:.0f} web requests.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="opraah_scraper", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env", help="path to .env (default: project .env)")
    ap.add_argument("--data-dir", help="override data directory (default: ./data)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("roster", help="import roster files and report counts")
    r.add_argument("--input", nargs="+", required=True)
    r.add_argument("--review-out")
    r.add_argument("--dry-run", action="store_true", help="report only, do not write the database")
    r.set_defaults(func=cmd_roster)

    run = sub.add_parser("run", help="collect accounts from the input files")
    run.add_argument("--input", nargs="+", required=True, help="roster CSV(s) or text file(s) of handles")
    run.add_argument("--due", action="store_true",
                     help="only accounts due: full every OPRAAH_FULL_EVERY_DAYS (30), light every OPRAAH_LIGHT_EVERY_DAYS (7)")
    run.add_argument("--depth", choices=["full", "light"], help="force depth when not using --due (default full)")
    run.add_argument("--limit", type=int, help="collect at most N accounts this run")
    run.add_argument("--only", help="comma-separated handles from the input to collect")
    run.add_argument("--detail", choices=["signals", "all", "none"],
                     help="which post pages to read: signals (default) = branded/collab posts and accounts without Meta")
    run.add_argument("--no-meta", action="store_true", help="skip Meta Business Discovery")
    run.add_argument("--no-web", action="store_true", help="skip logged-out public pages")
    run.add_argument("--no-wait-on-limit", action="store_true",
                     help="stop a source at the first refusal instead of pausing 15/30/60 min")
    run.add_argument("--new-run", action="store_true", help="start a new run instead of resuming an unfinished one")
    run.add_argument("--no-raw", action="store_true", help="do not keep raw responses under data/raw/")
    run.set_defaults(func=cmd_run)

    c = sub.add_parser("check-meta", help="test the Meta token with one Business Discovery call")
    c.add_argument("--username", default="gamerfleetog")
    c.set_defaults(func=cmd_check_meta)

    y = sub.add_parser("youtube", help="collect YouTube channels/videos for the roster's YouTube links")
    y.add_argument("--input", nargs="+", required=True)
    y.add_argument("--limit", type=int)
    y.add_argument("--only", help="comma-separated YouTube refs, e.g. @wackyteen")
    y.add_argument("--due", action="store_true", help="skip channels collected in the last 7 days")
    y.add_argument("--allow-search", action="store_true", help="resolve legacy /c/ links via search (100 units each)")
    y.set_defaults(func=cmd_youtube)

    e = sub.add_parser("export", help="regenerate CSVs and the per-creator knowledge base (data/kb)")
    e.add_argument("--to", help="also mirror data/kb into this folder (e.g. a Google Drive for desktop folder)")
    e.set_defaults(func=cmd_export)

    st = sub.add_parser("status", help="last run summary and throughput")
    st.set_defaults(func=cmd_status)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

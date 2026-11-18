"""Markdown wiki generated from SQLite (LLM-wiki pattern: linked pages, every claim cites a post).

data/wiki/
  index.md            all creators and brands
  SCHEMA.md           evidence rules
  creators/<handle>.md
  brands/<handle>.md
Pages are regenerated on every export; edit config/brands.csv or review_status in the DB,
not the pages.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from . import kb
from .brands import BRAND_WORK_KINDS
from .metrics import summarize
from .topics import creator_themes, load_taxonomy

SCHEMA = """# Evidence rules

- SQLite (`data/opraah.db`) is the source of truth. These pages are generated from it.
- The stable creator id is the roster **vendor code**. The Instagram account is tracked by its
  platform ids, so a renamed handle keeps its history. A handle shared by two vendor codes is
  never merged: both pages list both codes.
- Every metric shows its source and observation time. Missing metrics are blank, never 0.
  Averages use only posts where the metric was observed, with coverage such as `5 of 21 Reels`.
- Engagement rate per post = (likes + comments) / followers x 100. The page shows the median.
- `views` = Meta Business Discovery `view_count` (Reels). `plays` = Instagram's public play count.
  They are reported separately.
- A post geotag is where content was posted, not where the creator lives. The creator's city
  comes from the roster.
- Brand edges: collaboration_disclosed, coauthor, collab_post_owned_by_brand, product_integration,
  event_association, mention, hashtag, creator_collab. Each cites its post and the supporting
  caption text or metadata. No edge claims payment. `needs_review` edges await a human check.
"""


def _slug(handle: str) -> str:
    return re.sub(r"[^a-z0-9._-]", "_", handle.lower())


def _fmt_int(v) -> str:
    return "—" if v is None else f"{v:,}"


def _cell(text: str | None, limit: int = 90) -> str:
    t = (text or "").replace("\n", " ").replace("|", "/").strip()
    return t[:limit] + ("…" if len(t) > limit else "")


def build(conn, out_dir: Path, now: datetime, n: int, days: int) -> dict:
    taxonomy = load_taxonomy()
    (out_dir / "creators").mkdir(parents=True, exist_ok=True)
    (out_dir / "brands").mkdir(parents=True, exist_ok=True)
    (out_dir / "SCHEMA.md").write_text(SCHEMA, encoding="utf-8")
    brand_pages: dict[str, list[dict]] = defaultdict(list)
    index_rows = []

    for acct in kb.accounts(conn):
        aid, handle = acct["account_id"], acct["current_username"]
        roster = kb.roster_for_handles(conn, acct["handles"])
        prof = kb.latest_profile(conn, aid)
        posts = kb.window_posts(conn, aid, now, n, days, taxonomy)
        stats = summarize(posts, prof.get("followers"), now)
        evid = kb.evidence(conn, aid)
        themes = creator_themes(posts, taxonomy)
        codes = ", ".join(f"`{r['vendor_code']}` ({r.get('tier') or 'roster'})" for r in roster) or "not on roster"
        cities = sorted({r["location"] for r in roster if r.get("location")})
        lines = [
            f"# @{handle}" + (f" · {prof['full_name']}" if prof.get("full_name") else ""),
            "",
            f"- Instagram: https://www.instagram.com/{handle}/",
            f"- Vendor codes: {codes}",
            f"- City (roster): {', '.join(cities) or '—'}",
            f"- Followers: **{_fmt_int(prof.get('followers'))}** "
            f"(source `{prof.get('followers_source') or '—'}`, observed {prof.get('followers_observed_at') or '—'})",
            f"- Bio: {_cell(prof.get('biography'), 200) or '—'}",
        ]
        if prof.get("bio_links"):
            lines.append("- Links: " + ", ".join(b["url"] for b in prof["bio_links"]))
        if len(acct["handles"]) > 1:
            lines.append("- Earlier handles: " + ", ".join("@" + h for h in acct["handles"][1:]))
        lines += [
            "", "## Sample", "",
            f"{stats['posts_in_sample']} posts ({_cell(str(stats['formats']))}), "
            f"{(stats['first_post_at'] or '—')[:10]} to {(stats['last_post_at'] or '—')[:10]}; "
            f"{stats['posts_last_30_days']} in the last 30 days.",
            "",
            "| Metric | Value | Coverage |", "|---|---:|---|",
            f"| Median likes | {_fmt_int(stats['median_likes'])} | {stats['er_coverage']} |",
            f"| Median comments | {_fmt_int(stats['median_comments'])} | {stats['er_coverage']} |",
            f"| Median engagement rate | {stats['median_er_pct'] if stats['median_er_pct'] is not None else '—'}% | {stats['er_coverage']} |",
            f"| Avg Reel views (Meta) | {_fmt_int(stats['avg_reel_views'])} | {stats['views_coverage']} |",
            f"| Avg Reel plays (public) | {_fmt_int(stats['avg_reel_plays'])} | {stats['plays_coverage']} |",
            "",
            "Themes (caption/hashtag heuristic): " + (", ".join(f"{t} ({c})" for t, c in themes) or "—"),
            "", "## Brands and collaborations", "",
        ]
        work = [e for e in evid if e["kind"] in BRAND_WORK_KINDS or e["kind"] == "creator_collab"]
        other = [e for e in evid if e not in work]
        if work:
            lines += ["| Counterpart | Edge | Post | Evidence | Review |", "|---|---|---|---|---|"]
            for e in work:
                link = (f"[@{e['brand_handle']}](../brands/{_slug(e['brand_handle'])}.md)"
                        if not e["brand_handle"].startswith("(") else e["brand_handle"])
                lines.append(f"| {link} | {e['kind']} | [{(e['taken_at'] or e['approx_date'] or '')[:10]}]({e['url']}) "
                             f"| {_cell(e['evidence_text'])} | {e['review_status']} |")
                brand_pages[e["brand_handle"]].append({**e, "creator": handle})
        else:
            lines.append("No collaboration evidence in collected posts.")
        if other:
            lines += ["", "Mentions, events and brand hashtags:", ""]
            for e in other[:40]:
                lines.append(f"- @{e['brand_handle']} · {e['kind']} · [{(e['taken_at'] or e['approx_date'] or '')[:10]}]({e['url']})"
                             f" · {_cell(e['evidence_text'], 70)}")
                if e["brand_kind"] == "brand":
                    brand_pages[e["brand_handle"]].append({**e, "creator": handle})
        lines += ["", "## Posts in the window", "",
                  "| Date (IST) | Format | Likes | Comments | Views | Plays | ER % | Caption |",
                  "|---|---|---:|---:|---:|---:|---:|---|"]
        for p in posts:
            lines.append(f"| [{p['date_ist']}]({p['url']}) | {p.get('format') or ''}"
                         f"{' (collab)' if p['relation'] != 'owner' else ''} | {_fmt_int(p['likes'])} | "
                         f"{_fmt_int(p['comments'])} | {_fmt_int(p['views'])} | {_fmt_int(p['plays'])} | "
                         f"{p['er_pct'] if p['er_pct'] is not None else '—'} | {_cell(p.get('caption'), 70)} |")
        status = conn.execute("""SELECT status, coverage_json, finished_at FROM run_account WHERE username=?
                                 ORDER BY run_id DESC LIMIT 1""", (handle,)).fetchone()
        if status:
            lines += ["", f"_Last collection: {status['status']} at {status['finished_at']}. "
                          f"Coverage: `{status['coverage_json']}`_"]
        (out_dir / "creators" / f"{_slug(handle)}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        index_rows.append((handle, codes, prof.get("followers"), stats["median_er_pct"],
                           ", ".join(t for t, _ in themes[:3])))

    brand_info = {r["handle"]: dict(r) for r in conn.execute("SELECT * FROM brand")}
    for bh, items in brand_pages.items():
        if bh.startswith("("):
            continue
        info = brand_info.get(bh, {})
        lines = [f"# @{bh}" + (f" · {info['name']}" if info.get("name") else ""), "",
                 f"- Category: {info.get('category') or '—'}",
                 f"- Kind: {info.get('kind') or 'unknown'}", "",
                 "| Creator | Edge | Post | Evidence |", "|---|---|---|---|"]
        seen = set()
        for e in items:
            key = (e["creator"], e["kind"], e["shortcode"])
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"| [@{e['creator']}](../creators/{_slug(e['creator'])}.md) | {e['kind']} | "
                         f"[{(e['taken_at'] or e['approx_date'] or '')[:10]}]({e['url']}) | {_cell(e['evidence_text'])} |")
        (out_dir / "brands" / f"{_slug(bh)}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    idx = ["# Opraah creator knowledge base", "", f"Generated {now.isoformat()}. See [SCHEMA.md](SCHEMA.md).", "",
           "## Creators", "", "| Creator | Vendor codes | Followers | Median ER % | Themes |", "|---|---|---:|---:|---|"]
    for h, codes, f, er, th in sorted(index_rows):
        idx.append(f"| [@{h}](creators/{_slug(h)}.md) | {codes} | {_fmt_int(f)} | {er if er is not None else '—'} | {th} |")
    idx += ["", "## Brands", ""]
    for bh in sorted(b for b in brand_pages if not b.startswith("(")):
        name = brand_info.get(bh, {}).get("name")
        idx.append(f"- [@{bh}](brands/{_slug(bh)}.md)" + (f" · {name}" if name else "")
                   + f" · {len({e['creator'] for e in brand_pages[bh]})} creator(s)")
    (out_dir / "index.md").write_text("\n".join(idx) + "\n", encoding="utf-8")
    return {"creators": len(index_rows), "brands": len([b for b in brand_pages if not b.startswith("(")])}

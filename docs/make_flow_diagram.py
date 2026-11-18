"""Generate docs/scraper_flow.excalidraw (open at https://excalidraw.com → Open, or the VS Code Excalidraw extension).

Edit the boxes/arrows below and re-run:  .venv/bin/python docs/make_flow_diagram.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path

OUT = Path(__file__).with_name("scraper_flow.excalidraw")
rnd = random.Random(7)
elements: list[dict] = []

COLORS = {
    "input": "#ffec99", "control": "#d0bfff", "meta": "#a5d8ff", "web": "#b2f2bb", "logic": "#ffd8a8",
    "guard": "#ffc9c9", "store": "#e9ecef", "output": "#c3fae8", "future": "#ffffff", "note": "#fff9db",
}


def _base(kind: str, x: float, y: float, w: float, h: float, **kw) -> dict:
    return {
        "id": kw.pop("id", f"{kind}-{rnd.randrange(10**12)}"), "type": kind, "x": x, "y": y, "width": w, "height": h,
        "angle": 0, "strokeColor": kw.pop("stroke", "#1e1e1e"), "backgroundColor": kw.pop("bg", "transparent"),
        "fillStyle": "solid", "strokeWidth": kw.pop("sw", 2), "strokeStyle": kw.pop("style", "solid"),
        "roughness": 1, "opacity": 100, "groupIds": [], "frameId": None,
        "roundness": kw.pop("roundness", {"type": 3}), "seed": rnd.randrange(1, 2**31),
        "version": 1, "versionNonce": rnd.randrange(1, 2**31), "isDeleted": False, "boundElements": [],
        "updated": 1763424000000, "link": None, "locked": False, **kw,
    }


def _text(x, y, w, h, text, size=16, container=None, align="center", color="#1e1e1e") -> dict:
    lines = text.split("\n")
    t = _base("text", x, y, w, h, stroke=color, roundness=None, text=text, originalText=text, fontSize=size,
              fontFamily=2, textAlign=align, verticalAlign="middle" if container else "top",
              containerId=container, autoResize=True, lineHeight=1.25)
    tw = max(len(l) for l in lines) * size * 0.55
    th = len(lines) * size * 1.25
    if container is None:
        t["width"], t["height"] = tw, th
    else:
        t["width"], t["height"] = min(tw, w - 10), th
        t["x"], t["y"] = x + (w - t["width"]) / 2, y + (h - th) / 2
    return t


def box(key, x, y, w, h, text, kind="logic", shape="rectangle", size=16, dashed=False, align="center") -> dict:
    b = _base(shape, x, y, w, h, id=key, bg=COLORS[kind], style="dashed" if dashed else "solid",
              roundness={"type": 3} if shape == "rectangle" else {"type": 2})
    t = _text(x, y, w, h, text, size, container=key, align=align)
    b["boundElements"].append({"type": "text", "id": t["id"]})
    elements.extend([b, t])
    return b


def label(x, y, text, size=20, color="#1e1e1e"):
    elements.append(_text(x, y, 0, 0, text, size, color=color))


def frame(x, y, w, h, title, color="#868e96"):
    f = _base("rectangle", x, y, w, h, stroke=color, sw=1, style="dashed", roundness={"type": 3})
    elements.append(f)
    label(x + 14, y + 10, title, 18, color)


def _anchor(b, side):
    x, y, w, h = b["x"], b["y"], b["width"], b["height"]
    return {"r": (x + w, y + h / 2), "l": (x, y + h / 2), "b": (x + w / 2, y + h), "t": (x + w / 2, y)}[side]


def arrow(a, b, sa="r", sb="l", text=None, dashed=False, color="#1e1e1e", via=None):
    x1, y1 = _anchor(a, sa)
    x2, y2 = _anchor(b, sb)
    pts = [[0, 0]] + ([[vx - x1, vy - y1] for vx, vy in via] if via else []) + [[x2 - x1, y2 - y1]]
    ar = _base("arrow", x1, y1, abs(x2 - x1), abs(y2 - y1), stroke=color, style="dashed" if dashed else "solid",
               roundness={"type": 2}, points=pts, lastCommittedPoint=None,
               startBinding={"elementId": a["id"], "focus": 0, "gap": 4},
               endBinding={"elementId": b["id"], "focus": 0, "gap": 4},
               startArrowhead=None, endArrowhead="arrow", elbowed=False)
    a["boundElements"].append({"type": "arrow", "id": ar["id"]})
    b["boundElements"].append({"type": "arrow", "id": ar["id"]})
    elements.append(ar)
    if text:
        mx, my = x1 + pts[len(pts) // 2][0], y1 + pts[len(pts) // 2][1]
        if len(pts) == 2:
            mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        t = _text(mx, my, 0, 0, text, 13, color="#495057")
        t["x"] -= t["width"] / 2
        t["y"] -= t["height"] + 4
        elements.append(t)
    return ar


# ------------------------------------------------------------------ title
label(40, 20, "Opraah roster scraper → creator knowledge base  (opraah_scraper, v1)", 30)
label(40, 62, "Colours: yellow = input · purple = control · blue = Meta API · green = logged-out web · "
              "orange = logic · red = safety · grey = storage · teal = outputs · dashed = future", 15, "#495057")

# ------------------------------------------------------------------ 1. inputs
frame(40, 110, 360, 560, "1. Input  (roster.py)")
r1 = box("in_ex", 70, 160, 300, 70, "exclusive-roster.csv\n(222 rows)", "input")
r2 = box("in_nx", 70, 250, 300, 70, "non-exclusive-roster.csv\n(1,742 rows)", "input")
r3 = box("in_txt", 70, 340, 300, 60, "or handles.txt / username CSV", "input")
norm = box("norm", 70, 440, 300, 110,
           "Normalize handles\nURL · @ · ?hl=en · case\n1,393 distinct handles\nshared handles keep BOTH vendor codes",
           "logic", size=15)
review = box("review", 70, 580, 300, 60, "roster_review.csv\n523 missing · 46 shared · 19 both tiers", "output", size=14)
for r in (r1, r2, r3):
    arrow(r, norm, "b" if r is r3 else "l", "l" if r is not r3 else "t",
          via=[(55, r["y"] + 35), (55, 495)] if r is not r3 else None)
arrow(norm, review, "b", "t")

# ------------------------------------------------------------------ 2. scheduler
frame(440, 110, 330, 560, "2. Run control  (cli.py)")
cmd = box("cmd", 465, 160, 280, 90, "python -m opraah_scraper run\n--input … [--due] [--limit]\n[--only] [--detail]", "control", size=15)
due = box("due", 465, 290, 280, 110, "--due decides depth per handle\nfull: every 30 days\nlight: every 7 days\nelse skipped_not_due", "control",
          shape="diamond", size=14)
resume = box("resume", 465, 440, 280, 90, "Resume: unfinished run with\nsame input → skip finished\naccounts (Ctrl-C safe)", "control", size=15)
loop = box("loop", 465, 570, 280, 70, "for each handle (one at a time)", "control")
arrow(norm, cmd, "r", "l")
arrow(cmd, due, "b", "t")
arrow(due, resume, "b", "t")
arrow(resume, loop, "b", "t")

# ------------------------------------------------------------------ 3. waterfall
frame(810, 110, 1180, 880, "3. Per-creator waterfall  (collect.py)")
meta = box("meta", 840, 160, 330, 120,
           "① Meta Business Discovery  (free, official)\n1 call ≈ profile + 50 posts\nlikes · comments · Reel views",
           "meta", size=15)
mdec = box("mdec", 1230, 160, 260, 120, "Meta OK?", "meta", shape="diamond")
skipweb = box("skipweb", 1560, 160, 400, 120,
              "light run + Meta OK\n→ done: no web requests\n(weekly refresh is ~1 request)", "logic", size=15)
prof = box("prof", 840, 340, 330, 130,
           "② Logged-out profile page\nbio links · verified · ig_pk + graph_id\nfirst 12 timeline posts (owner →\ncollab posts by brands)",
           "web", size=14)
tl = box("tl", 1230, 340, 330, 130,
         "Timeline pages (12 each)\nuntil 30 posts + 3 pins AND oldest\n> 30 days (max 6 pages)\nfull runs only", "web", size=14)
reels = box("reels", 1620, 340, 340, 130,
            "③ Reels tab (1 page)\nonly if Reel views missing\n→ public play counts", "web", size=14)
detail = box("detail", 840, 530, 330, 150,
             "④ Post pages (selective, cached)\n--detail signals: #ad #collab,\n@mentions, promo words, brand tags,\ncollab posts, ALL posts if no Meta\n→ co-authors · tags · geotag · audio",
             "web", size=13)
window = box("window", 1230, 530, 330, 150,
             "⑤ Window\nlatest 30 by publish time\n∪ every post in last 30 days\n(old pins can't push out new posts)",
             "logic", size=14)
brands = box("brands", 1620, 530, 340, 150,
             "⑥ Brand evidence (brands.py)\ncoauthor · disclosed · owned_by_brand\nproduct_integration · event · mention\nhashtag · creator_collab\nconfig/brands.csv registry",
             "logic", size=13)
status = box("status", 1230, 760, 330, 120,
             "⑦ Status per handle\nok · partial · private · not_found\nrate_limited · challenge · error\n(refused partials stay due)",
             "logic", size=14)

arrow(loop, meta, "r", "l", via=[(790, 605), (790, 220)])
arrow(meta, mdec, "r", "l")
arrow(mdec, skipweb, "r", "l", "yes + light")
arrow(mdec, prof, "b", "t", "no / unavailable / full run", via=[(1360, 310), (1005, 310)])
arrow(prof, tl, "r", "l", "has next page")
arrow(tl, reels, "r", "l", "views missing")
arrow(prof, detail, "b", "t")
arrow(detail, window, "r", "l")
arrow(window, brands, "r", "l")
arrow(brands, status, "b", "r", via=[(1790, 820)])
arrow(skipweb, window, "r", "t", dashed=True, via=[(1980, 220), (1980, 505), (1395, 505)])

yt = box("yt", 840, 740, 330, 200,
         "⑧ YouTube  (`youtube` command)\nYouTube Data API key, ~3 units/creator\nsubs · views · last 30 videos +\n30 days: views, likes, comments,\nduration, tags, paid-promotion flag",
         "meta", size=13)
arrow(yt, status, "r", "l", "same brand rules")

# ------------------------------------------------------------------ 4. safety
frame(810, 1020, 1180, 200, "4. Safety rails on every request  (budget.py)", "#e03131")
pace = box("pace", 840, 1070, 260, 120, "Pacing\n2–4 s jitter, 1 at a time\n≤ 300 web req/hour\n≤ 180 Meta req/hour", "guard", size=14)
back = box("back", 1130, 1070, 260, 120, "429 / 'wait a few minutes'\n→ pause 15 → 30 → 60 min\n→ stop that source", "guard", size=14)
wall = box("wall", 1420, 1070, 250, 120, "Login wall / challenge\n→ stop web immediately\nMeta token error → stop Meta", "guard", size=14)
never = box("never", 1700, 1070, 260, 120, "Never: login, stealth,\nproxy rotation, CAPTCHA\nsolving, fake accounts", "guard", size=14)

# ------------------------------------------------------------------ 5. storage
frame(2030, 110, 420, 880, "5. Storage  (data/)")
db = box("db", 2060, 160, 360, 420,
         "SQLite  data/opraah.db\n(source of truth)\n\nroster_creator · roster_link\nig_account (ig_pk, graph_id)\nig_handle (aliases, due dates)\nprofile_snapshot  (append-only)\npost · account_post\npost_metric  (append-only,\nsource + time, NULL ≠ 0)\nbrand · brand_evidence\nrun · run_account · request_log",
         "store", size=14)
raw = box("raw", 2060, 620, 360, 100, "data/raw/YYYY-MM-DD/<handle>/\ngzipped raw responses (provenance,\nre-parse without re-fetching)", "store", size=14)
arrow(status, db, "b", "l", "commit after every step", via=[(1395, 950), (2005, 950), (2005, 370)])
arrow(brands, db, "r", "l", dashed=True)
arrow(db, raw, "b", "t", dashed=True)

# ------------------------------------------------------------------ 6. outputs
frame(2490, 110, 420, 880, "6. Outputs  (export.py · wiki.py)")
csvs = box("csvs", 2520, 160, 360, 200,
           "data/exports/\ncreators.csv  (1 row per vendor code)\nposts.csv  (window posts + metrics)\nbrand_evidence.csv  (post URL + proof)\nrun_status.csv  (requests, coverage)",
           "output", size=14)
wk = box("wk", 2520, 400, 360, 190,
         "data/kb/  (one folder per creator)\nindex.csv · brands.csv · brands/*.md\ncreators/<CODE>__<handle>/\nprofile.md · profile.json\ninstagram_posts · youtube_videos\nbrand_evidence · history (CSV)\n→ mirrored to Google Drive (--to)", "output", size=13)
bot = box("bot", 2520, 630, 360, 120, "FUTURE: chatbot / agent\n'30 creators for brand X'\n'brands to pitch creator Y'", "future",
          size=15, dashed=True)
pg = box("pg", 2520, 790, 360, 110, "FUTURE: sync to internal-be\nPostgres chatbot.* views", "future", size=15, dashed=True)
arrow(db, csvs, "r", "l")
arrow(db, wk, "r", "l")
arrow(wk, bot, "b", "t", dashed=True)
arrow(db, pg, "r", "l", dashed=True, via=[(2460, 450), (2460, 845)])

# ------------------------------------------------------------------ 7. improvement notes
frame(40, 720, 730, 500, "7. Known gaps → ideas to improve the flow", "#f08c00")
box("gaps", 70, 770, 670, 420,
    "• Reels tab: only first 12 Reels logged-out → plays for ~5 of 21 Reels\n"
    "    (Meta view_count fixes this once the token is set)\n"
    "• No 'Paid partnership' label logged-out → rely on #ad/#collab + co-authors\n"
    "• Unknown counterparts (@someone) default to 'unknown' → grow config/brands.csv\n"
    "• Themes are keyword rules (topics.json) → later: LLM / vision / transcripts\n"
    "• 300 req/h web cap is a guess → tune after the 10-account pilot (status cmd)\n"
    "• No audience data (city/age/gender) → creator-consented Instagram Login\n"
    "• Scheduling is manual → launchd/cron weekly `run --due`\n"
    "• Full runs paginate timeline even with Meta OK (to find brand-owned collabs)\n"
    "    → could skip when the account rarely has collabs",
    "note", size=15, align="left")

doc = {"type": "excalidraw", "version": 2, "source": "https://excalidraw.com", "elements": elements,
       "appState": {"gridSize": None, "viewBackgroundColor": "#ffffff"}, "files": {}}
OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
print(f"wrote {OUT} ({len(elements)} elements)")

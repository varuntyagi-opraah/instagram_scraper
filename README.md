# Opraah roster scraper and creator knowledge base

One command takes the roster (or any list of Instagram handles) and builds a dated,
source-linked profile for every creator across Instagram and YouTube: followers and subscribers,
bio, the latest 30 posts/videos plus everything from the last 30 days, likes, comments, Reel
views and plays, shares (when Meta serves them), engagement, branded vs organic performance,
growth, posting rhythm, caption language, content themes, and the brands each creator has worked
with, each tied to its post. It costs nothing to run.

- **Source of truth:** `data/opraah.db` (SQLite; keep it local, never in Drive)
- **Knowledge base, one folder per creator:** `data/kb/` (see "Output layout" below)
- **Flat review files:** `data/exports/creators.csv`, `posts.csv`, `youtube_videos.csv`, `brand_evidence.csv`, `run_status.csv`, `roster_review.csv`
- Docs: [META_SETUP](docs/META_SETUP.md) · [YOUTUBE_SETUP](docs/YOUTUBE_SETUP.md) · [DRIVE](docs/DRIVE.md) · [RESEARCH](docs/RESEARCH.md) · [REVIEW 18 Nov](docs/REVIEW_2026-11-18.md) · flow diagram `docs/scraper_flow.excalidraw`

## Setup (once)

```bash
cd ~/Downloads/insta_scraper
uv venv --python 3.11 && uv pip install -e . pytest
cp .env.example .env          # then add the Meta token, see docs/META_SETUP.md
.venv/bin/python -m pytest -q # 89 offline tests, no network
```

Without a Meta token the scraper still works, logged-out only. That is slower (about 34
requests per creator instead of about 5) and has no Reel view counts.

## Everyday commands

```bash
# Check the roster files: counts, shared handles, rows without a handle (writes roster_review.csv)
.venv/bin/python -m opraah_scraper roster --input ~/Downloads/opraah_data/exclusive-roster-2026-11-16.csv ~/Downloads/opraah_data/non-exclusive-roster-2026-11-16.csv --dry-run

# Collect everything in the files (first full pass). Resumable: run it again after Ctrl-C.
.venv/bin/python -m opraah_scraper run --input ~/Downloads/opraah_data/*-roster-2026-11-16.csv

# Weekly/monthly refresh: only accounts that are due (full every 30 days, light every 7)
.venv/bin/python -m opraah_scraper run --input ~/Downloads/opraah_data/*-roster-2026-11-16.csv --due

# Pilot or one-off checks
.venv/bin/python -m opraah_scraper run --input pilot.csv --limit 10
.venv/bin/python -m opraah_scraper run --input roster.csv --only gamerfleetog,remo.rizz

# YouTube (official API key; ~3 units per channel, roster fits in one day's free quota)
.venv/bin/python -m opraah_scraper youtube --input ~/Downloads/opraah_data/*-roster-2026-11-16.csv --due

.venv/bin/python -m opraah_scraper check-meta --username gamerfleetog   # test the Meta token
.venv/bin/python -m opraah_scraper status                               # last run, requests, throughput estimate
.venv/bin/python -m opraah_scraper export                               # regenerate CSVs + data/kb
.venv/bin/python -m opraah_scraper export --to "<Drive folder>"         # also mirror data/kb into Drive
```

**Input:** a CSV with an `Instagram` (or `username` / `handle`) column, plus optional `Vendor Code`,
`Type`, `Location` and the other roster columns, or a `.txt` file with one handle or profile
URL per line. URLs, `@handles`, `?hl=en` and letter case are normalized. Each distinct handle is
fetched once. A handle listed under two vendor codes stays linked to both and is never merged.

## How it collects (per creator)

| Step | Source | Gives | Requests |
|---|---|---|---|
| 1 | Meta Business Discovery (official, free) | followers, bio, website, posts with date, caption, likes, comments, Reel views | 1–2 |
| 2 | Logged-out profile page (full runs, or when Meta can't serve the account) | bio links, verified, both platform ids, collab posts owned by other accounts | 1 + 1 per 12 posts |
| 3 | Logged-out Reels tab (only if Reel views are missing) | public play counts | 1 |
| 4 | Logged-out post pages (selective, cached for good) | co-authors, tagged users, geotag, audio; counts for accounts without Meta | branded/collab posts only |

Accounts Meta can't serve (personal, age-gated) go entirely through steps 2–4.
`--detail all|signals|none` controls step 4 (default `signals`: posts with disclosures, @mentions,
promo language or brand hashtags, collab posts, and every post of accounts without Meta).

**Window.** Collection keeps the latest 30 posts by publication time, plus every post from the
last 30 days. Old pinned posts never push out recent ones.

**Statuses** in `run_status.csv`: `ok`, `partial` (some surface ended early; coverage says which),
`private`, `not_found`, `rate_limited`, `challenge`, `error`, `skipped_not_due`. A `partial` caused
by a refusal is retried by the next `--due` run.

**Politeness.** Requests are spaced 2–4 s apart, capped at 300 logged-out requests per hour,
one at a time. When Instagram refuses (429, or "please wait a few minutes"), collection pauses
15, then 30, then 60 minutes, then stops that source for the run. A login wall stops logged-out
collection immediately. Progress is saved after every request. The scraper never logs in,
spoofs a browser fingerprint, rotates proxies or solves CAPTCHAs. Run it from an office or home
connection. Cloud servers tend to get login walls on logged-out pages, though the Meta API works
from anywhere.

## Measured so far

- Live on 17 Nov 2026, logged-out only (no Meta token): `gamerfleetog` returned `ok`. It took
  34 requests and 101 s. The window held 30 posts: 21 Reels and 9 carousels, including one posted
  that day. Likes and comments were present on all 30, and median ER was 8.0%. Plays were
  available for 5 of 21 Reels. Brands found: Samsung India (3 posts), Lenovo Legion India,
  Amazon News India and YouTube India.
- At that pace, a no-Meta first pass of 1,393 handles is about 47,000 logged-out requests:
  about 160 hours at the default cap of 300 per hour. The cap is a conservative starting value,
  to be tuned after the pilot. With Meta, most accounts need 1 Meta call plus the logged-out
  profile, timeline and only the branded post pages (roughly 10). Later monthly runs only read
  new posts. Measure the real numbers on the 10-account pilot with `status` before running the
  full roster.

## Metrics rules

- ER per post = `(likes + comments) / followers × 100`. Creator ER is the median over posts with
  both counts. Coverage is always shown, for example `30 of 30 posts`.
- `views` is Meta's Reel `view_count`. `plays` is Instagram's public play count. They are never mixed.
- Hidden or unavailable metrics are blank, never 0.
- A post geotag is a *content location*. The creator's city comes from the roster.

## Brand evidence

`brand_evidence.csv` has one row per (post, counterpart, kind) with the supporting caption text
or metadata and a `rule_id`. Kinds: `collaboration_disclosed`, `coauthor`,
`collab_post_owned_by_brand`, `product_integration`, `event_association`, `mention`, `hashtag`,
`creator_collab` (the counterpart is on the roster). `needs_review` rows wait for a person to
check them. Set `review_status` to `confirmed` or `rejected` in the DB and reruns keep your
decision.

To teach it brands, add rows to `config/brands.csv` (handle, name, category, hashtag aliases;
`galaxy*` means any hashtag starting with "galaxy"). Edit `config/topics.json` to change themes.
The built-in defaults are in `opraah_scraper/default_topics.json`.

## Files

```
opraah_scraper/
  cli.py            commands
  roster.py         roster/handle parsing and review report
  collect.py        per-creator waterfall, window, selective post pages, evidence
  sources/meta_bd.py  Meta Business Discovery client (+ error/usage handling)
  sources/web.py      logged-out pages via Instaloader 4.16; optional Obscura 0.2.7 for post pages
  parse/            embedded-JSON parsing (ported from the GamerFleet pilot), caption helpers
  budget.py         pacing, hourly caps, backoff, stop rules
  brands.py         evidence rules + brand registry
  sources/youtube.py, collect_youtube.py   YouTube Data API collector
  derive.py         branded vs organic ER, consistency, growth, rhythm, language
  profiles.py       one structured record per creator (IG + YouTube)
  kb_export.py      data/kb folders + Drive mirror
  metrics.py, topics.py, kb.py, export.py, wiki.py
  schema.sql        SQLite schema
config/brands.csv   brand registry (edit me)
tests/              offline tests; fixtures are real pages saved in the GamerFleet pilot
docs/               META_SETUP.md, RESEARCH.md
```

The schema keeps every observation: `profile_snapshot` and `post_metric` rows are append-only
and carry a source and timestamp. A monthly history therefore builds up automatically, ready for
the chatbot ("who grew fastest", "who posted with Samsung in the last 90 days").

## Output layout (`data/kb/`, rebuilt on every export)

```
data/kb/
  README.md, SCHEMA.md
  index.csv                      one row per creator (vendor code): filter/sort here
  index.md                       same, linked to each folder
  brands.csv                     brand → creators, posts, last post date
  brands/<handle>.md             every creator linked to that brand, with post links
  creators/<VENDORCODE>__<handle>/
    profile.md                   readable summary; every brand claim cites its post
    profile.json                 the same, structured (for the chatbot/agent)
    instagram_posts.csv          latest 30 + last 30 days, with metrics and brand flags
    youtube_videos.csv           same window for YouTube
    brand_evidence.csv           both platforms: brand, edge, post URL, supporting text, rule
    history.csv                  followers / subscribers over time
```

The vendor code is the stable id (handles change). A handle listed under two vendor codes gets a
folder per code, each noting the other. Creators without social links still get a folder with
their roster data. The folder is built in a temporary directory and swapped in, so a sync client
never sees a half-written export.

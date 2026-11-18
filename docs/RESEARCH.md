# Research notes (17 Nov 2026): how to get roster data fast, accurately and for free

## What each route actually returns

These findings were checked against real responses saved in `tests/fixtures/` and against
Meta's documentation.

| Route | Returns | Misses | Requests per creator |
|---|---|---|---|
| Meta Business Discovery | followers, follows, media_count, bio, website; per post timestamp, caption, like_count, comments_count, Reel view_count, permalink | co-authors, tagged users, geotags, paid-partnership label, collab posts owned by others, personal and age-gated accounts | 1 per 50 posts |
| Logged-out profile page | follower_count, bio, bio_links, verified, Instagram pk **and** Graph id (lets Meta and web records be joined); 12 timeline entries with caption, owner and an accessibility caption that includes the date | likes and comments per post | 1 |
| Logged-out timeline query (doc_id 28904005159232689, as Instaloader 4.16 sends it) | 12 more timeline entries per page | same as above | 1 per 12 |
| Logged-out Reels tab | like_count, comment_count, **play_count** for 12 Reels | captions and dates; no logged-out pagination | 1 |
| Logged-out post page | taken_at, caption, likes, comments, coauthor_producers, usertags, location, audio, Instagram's image description | views and plays, paid-partnership label, sponsor tags (absent for all 41 GamerFleet posts) | 1 per post |
| Logged-in web app (old `grid.py`, browser extensions) | everything above plus the paid-partnership label | — | ~3, but it automates a real account, which risks checkpoints and blocks. **Not used.** |

## Tools

- **Instaloader 4.16** (MIT) is used as a library for two things only: `get_page_data` and the
  logged-out timeline query. Its `Profile.get_posts()` makes one extra request per post when
  logged out, so we read the timeline nodes directly and fetch post pages only when needed.
  Its page loader reports 404, 429 and login redirects, which the stop rules depend on.
- **Obscura 0.2.7** (Apache-2.0) worked in the pilot for post pages. Its raw `--dump original`
  mode hides the HTTP status (a 429 comes back as an empty body with exit code 0), so it is an
  optional fetcher (`OPRAAH_WEB_FETCHER=obscura`) and an empty body is treated as a refusal. It is
  never run with `--stealth`.
- **Scrapling** is not used. Its Instagram value is TLS and fingerprint impersonation and
  anti-bot bypass (StealthyFetcher). We parse JSON, so its adaptive HTML selectors add nothing.
- **gallery-dl / yt-dlp** are good later for media download (OCR/transcription phase).
- **Browser-extension exporters** read the logged-in web app's own API responses. That requires
  a logged-in account, which was ruled out for v1.

## Deliberately excluded

Fingerprint spoofing, proxy rotation, CAPTCHA solving, fake or rotated accounts, and retrying
harder after a refusal. These are anti-bot circumvention, they put Opraah's accounts and IP at
risk, and Meta's terms restrict automated collection. The scraper limits itself to public data
for the roster at a slow, single-threaded pace, and the official API carries most of the load.

## Later options

- **Creator-consented Instagram Login** for exclusive creators. It is free and official, and
  gives reach, saves, shares and audience city, age and gender: the "where is the audience"
  question no scraper can answer.
- A logged-in pass for paid-partnership labels, if Opraah accepts the account risk.

## Sources

- Instaloader: https://github.com/instaloader/instaloader (code read from the pinned 4.16 package)
- Obscura: https://github.com/h4ckf0r0day/obscura
- Scrapling: https://github.com/D4Vinci/Scrapling
- Business Discovery: https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/business_discovery
- Instagram API updates, 22 Apr 2026 (view_count includes crossposted Facebook views):
  https://developers.facebook.com/blog/post/2026/04/22/instagram-api-updates-for-partnerships-metrics-collaboration-and-engagement/
- LLM Wiki pattern: https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f

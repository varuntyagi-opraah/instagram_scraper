# YouTube Data API setup (free, about 10 minutes)

1. Go to https://console.cloud.google.com/, create a project (e.g. "opraah-creator-kb").
2. **APIs & Services → Library →** "YouTube Data API v3" → **Enable**.
3. **APIs & Services → Credentials → Create credentials → API key.** Restrict it: *API restrictions*
   → YouTube Data API v3 only.
4. Put it in `.env`: `YOUTUBE_API_KEY=AIza...`
5. Test on one channel:

```bash
.venv/bin/python -m opraah_scraper youtube --input ~/Downloads/opraah_data/exclusive-roster-2026-11-16.csv --limit 1
```

**Quota.** 10,000 units per day per project (resets at midnight Pacific, 13:30 IST). One channel
costs about 3 units, so the whole roster's ~530 YouTube links fit in one day. The scraper stops at
`YOUTUBE_DAILY_UNITS` (9,000) and continues the next day. Legacy `youtube.com/c/<name>` links (18
on the roster) need a 100-unit search: add `--allow-search`, or better, replace them with the
channel's `@handle` in the roster.

**What you get:** subscribers, total views, video count, country, channel keywords and topics; for
the latest 30 videos plus every video from the last 30 days: views, likes, comments, duration
(Shorts guessed as ≤ 3 min), tags, category, language, made-for-kids, and the creator's own
"includes paid promotion" flag. Brand rules also read titles and descriptions (#ad, "sponsored by",
"use code", @mentions, registered brand tags).

**Not available with an API key:** shares, watch time, retention, audience demographics. Those
need each creator to connect their channel (YouTube Analytics API, OAuth).

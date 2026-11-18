# Meta Business Discovery setup (free, about 30 minutes)

Business Discovery lets one Instagram professional account look up other professional
(Business or Creator) accounts by username. Each call returns followers, bio, website and recent
posts with date, caption, likes, comments and Reel `view_count`. It is part of Meta's Instagram
API with Facebook Login and costs nothing.

You do these steps yourself in Meta's tools. The scraper only reads the finished token from `.env`.

## 1. An Opraah Instagram professional account linked to a Facebook Page

- Use an Opraah-owned Instagram account, such as the agency account, and switch it to
  **Business** or **Creator** (Instagram → Settings → Account type and tools).
- Link it to a Facebook Page that Opraah manages (Instagram → Edit profile → Page, or Meta
  Business Suite → Settings → Instagram accounts).

## 2. A Meta developer app

1. Go to https://developers.facebook.com/apps, then **Create app**, and choose a **Business** type app.
2. In the app, add the **Instagram** product and choose **API setup with Facebook login**.
3. The app can stay in **Development mode**. Business Discovery works for tokens of people who
   have a role on the app (admin, developer or tester). `check-meta` confirms this.

## 3. A token with these permissions

`instagram_basic`, `instagram_manage_insights`, `pages_read_engagement`, `pages_show_list`
(add `business_management` if the Page sits in a Business Manager, and `ads_read` if Meta asks
for it because of a Business Manager page role).

There are two ways to get one:

- **Best, because it doesn't expire:** Business Settings → Users → **System users** → add one,
  assign the Page and the app as assets, then **Generate token** with the permissions above.
- **Quick:** Graph API Explorer (https://developers.facebook.com/tools/explorer). Select the app,
  choose **User token**, tick the permissions and generate. Then exchange it for a 60-day token:
  `GET https://graph.facebook.com/v27.0/oauth/access_token?grant_type=fb_exchange_token&client_id=APP_ID&client_secret=APP_SECRET&fb_exchange_token=SHORT_TOKEN`

## 4. The Instagram user id

In Graph API Explorer, with the token, run:

```
GET me/accounts?fields=name,instagram_business_account
```

Copy `instagram_business_account.id`. It starts with `1784…`.

## 5. Save and test

```bash
cp .env.example .env    # if not done yet
# edit .env:
# META_ACCESS_TOKEN=EAAG...
# META_IG_USER_ID=1784...
.venv/bin/python -m opraah_scraper check-meta --username gamerfleetog
```

A working token prints followers and the five newest posts. Common failures:

| Message | Meaning |
|---|---|
| `auth_error ... code=190` | The token expired or was revoked. Generate a new one. |
| `auth_error ... code=10/200` | A permission is missing from the token. |
| `unavailable ... 2207013` | That username is a personal account (or doesn't exist). Normal for some creators: the scraper falls back to public pages for them. |
| `rate_limited ... code=4/17/32/613` | Meta's hourly limit. The scraper pauses on its own. |

The token stays in `.env`, which is git-ignored. It is never written to the database, logs,
exports or raw files.

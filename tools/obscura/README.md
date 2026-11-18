# Obscura 0.2.7 (optional fetcher)

The scraper's default fetcher is Instaloader. Obscura is optional (`OPRAAH_WEB_FETCHER=obscura`)
and only used for post pages. These binaries are the macOS arm64 build used in the GamerFleet
pilot. They are git-ignored, so on another machine download the matching release from
https://github.com/h4ckf0r0day/obscura/releases and put `obscura` and `obscura-worker` here.

| File | SHA-256 |
|---|---|
| obscura | d09acc26fb1c5b0ea2ef297f55ea2430f765395d8cfd2eda78fcb50458153195 |
| obscura-worker | 241bc00417865ff34436e16c8c186ff67b1c28b4226f929e2ad556a3f3265c88 |
| release.tar.gz (as downloaded) | fc7e73f23058f18d6d5be983d83b24ba374d12167677657a307a9476c0f5a7c1 |

The scraper checks the `obscura` checksum before running it. If you upgrade, set
`obscura_sha256` in `opraah_scraper/config.py`. It is never run with `--stealth` or `--proxy`.

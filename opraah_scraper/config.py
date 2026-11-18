"""Settings from environment variables and an optional `.env` file.

Nothing here is secret except META_ACCESS_TOKEN, which is read from the
environment or `.env` and never written to the database, logs or exports.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value or "\t#" in value:
            value = value.split("#", 1)[0].strip()  # inline comment
        values[key.strip()] = value
    return values


@dataclass
class Settings:
    data_dir: Path = PROJECT_ROOT / "data"
    # Meta Business Discovery (Instagram API with Facebook Login)
    meta_access_token: str | None = None
    meta_ig_user_id: str | None = None
    graph_version: str = "v27.0"
    meta_hourly_cap: int = 180
    # YouTube Data API v3
    youtube_api_key: str | None = None
    youtube_daily_units: int = 9000   # Google's default project quota is 10,000/day; keep headroom
    kb_sync_dir: str | None = None    # mirror data/kb here after every export (e.g. Google Drive folder)
    # Logged-out public pages
    web_fetcher: str = "instaloader"  # or "obscura"
    obscura_path: Path = PROJECT_ROOT / "tools" / "obscura" / "obscura"
    obscura_sha256: str | None = "d09acc26fb1c5b0ea2ef297f55ea2430f765395d8cfd2eda78fcb50458153195"
    web_min_interval: float = 2.0
    web_max_interval: float = 4.0
    web_hourly_cap: int = 300
    # Backoff after Instagram pushes back (minutes): 15 -> 30 -> 60, then stop that source.
    backoff_minutes: tuple[int, ...] = (15, 30, 60)
    # Collection window: latest N posts plus every post from the last D days.
    window_posts: int = 30
    window_days: int = 30
    # Cadence for --due
    light_every_days: int = 7
    full_every_days: int = 30
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "opraah.db"

    @property
    def meta_configured(self) -> bool:
        return bool(self.meta_access_token and self.meta_ig_user_id)


def load_settings(env_file: Path | None = None, **overrides) -> Settings:
    env = _read_dotenv(env_file or PROJECT_ROOT / ".env")
    env.update({k: v for k, v in os.environ.items() if k.startswith(("META_", "OPRAAH_", "YOUTUBE_"))})

    def get(name: str, default=None):
        return env.get(name, default)

    s = Settings()
    if get("OPRAAH_DATA_DIR"):
        s.data_dir = Path(get("OPRAAH_DATA_DIR")).expanduser()
    s.meta_access_token = get("META_ACCESS_TOKEN") or None
    s.meta_ig_user_id = get("META_IG_USER_ID") or None
    s.graph_version = get("META_GRAPH_VERSION", s.graph_version)
    s.meta_hourly_cap = int(get("META_HOURLY_CAP", s.meta_hourly_cap))
    s.youtube_api_key = get("YOUTUBE_API_KEY") or None
    s.youtube_daily_units = int(get("YOUTUBE_DAILY_UNITS", s.youtube_daily_units))
    s.kb_sync_dir = get("OPRAAH_KB_SYNC_DIR") or None
    s.web_fetcher = get("OPRAAH_WEB_FETCHER", s.web_fetcher)
    if get("OPRAAH_OBSCURA_PATH"):
        s.obscura_path = Path(get("OPRAAH_OBSCURA_PATH")).expanduser()
    s.web_min_interval = float(get("OPRAAH_WEB_MIN_INTERVAL", s.web_min_interval))
    s.web_max_interval = float(get("OPRAAH_WEB_MAX_INTERVAL", s.web_max_interval))
    s.web_hourly_cap = int(get("OPRAAH_WEB_HOURLY_CAP", s.web_hourly_cap))
    s.window_posts = int(get("OPRAAH_WINDOW_POSTS", s.window_posts))
    s.window_days = int(get("OPRAAH_WINDOW_DAYS", s.window_days))
    s.light_every_days = int(get("OPRAAH_LIGHT_EVERY_DAYS", s.light_every_days))
    s.full_every_days = int(get("OPRAAH_FULL_EVERY_DAYS", s.full_every_days))
    for key, value in overrides.items():
        setattr(s, key, value)
    return s

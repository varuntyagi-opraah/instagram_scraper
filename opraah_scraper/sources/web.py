"""Logged-out public Instagram pages.

Default fetcher: Instaloader 4.16 (pinned) used as a library, only for
- InstaloaderContext.get_page_data(path): a public page's embedded GraphQL data, with proper
  404 / 429 / login-redirect handling;
- the logged-out profile timeline query (doc_id 28904005159232689, the same one
  Profile.get_posts() uses) for pagination, *without* the extra request per post that
  get_posts() makes.

Optional fetcher: Obscura 0.2.7 (`fetch URL --dump original`), the tool used in the
GamerFleet pilot, for post pages only. Its raw mode exposes no HTTP status, so an empty
body is treated conservatively as a refusal.

No login, no stealth flags, no proxy settings.
"""

from __future__ import annotations

import hashlib
import subprocess
import time
from pathlib import Path

from ..budget import Outcome, Pacer
from ..parse import web as webparse
from ..parse.embedded_json import page_data
from . import FetchResult, Recorder, null_recorder

TIMELINE_DOC_ID = "28904005159232689"
EMPTY_PAGES_BEFORE_BACKOFF = 5


def _passthrough_rate_controller(context):
    """Instaloader's own RateController silently sleeps ~3.5 min after 75 page loads per 11 min,
    inside what looks like one request. Our Pacer enforces the same window visibly (and logs it),
    so Instaloader's is replaced by one that never sleeps."""
    import instaloader

    class _PassThrough(instaloader.RateController):
        def wait_before_query(self, query_type):
            return None

        def handle_429(self, query_type):
            return None

    return _PassThrough(context)


def _classify_instaloader_error(exc: Exception) -> tuple[str, int | None]:
    import instaloader.exceptions as ie
    text = str(exc)
    if isinstance(exc, ie.QueryReturnedNotFoundException):
        return Outcome.NOT_FOUND.value, 404
    if isinstance(exc, ie.TooManyRequestsException):
        return Outcome.RATE_LIMITED.value, 429
    if isinstance(exc, ie.LoginRequiredException):
        return Outcome.LOGIN_WALL.value, 302
    if isinstance(exc, ie.AbortDownloadException) or "checkpoint_required" in text or "challenge_required" in text:
        return Outcome.CHALLENGE.value, 400
    if "Please wait a few minutes" in text or "401" in text:
        return Outcome.RATE_LIMITED.value, 401
    if "403" in text:
        return Outcome.RATE_LIMITED.value, 403
    return Outcome.ERROR.value, None


class WebClient:
    """Shared pacing, recording and refusal handling for logged-out requests."""

    def __init__(self, pacer: Pacer, recorder: Recorder = null_recorder, fetcher: str = "instaloader",
                 obscura_path: Path | None = None, obscura_sha256: str | None = None):
        self.pacer = pacer
        self.recorder = recorder
        self.fetcher = fetcher
        self._ctx = None
        self._obscura = None
        self._empty_streak = 0
        if fetcher == "obscura":
            self._obscura = ObscuraFetcher(obscura_path, obscura_sha256)

    @property
    def ctx(self):
        if self._ctx is None:
            import instaloader
            loader = instaloader.Instaloader(
                sleep=False, quiet=True, max_connection_attempts=1, request_timeout=30,
                download_pictures=False, download_videos=False, download_video_thumbnails=False,
                download_geotags=False, download_comments=False, save_metadata=False,
                compress_json=False, iphone_support=False, rate_controller=_passthrough_rate_controller)
            self._ctx = loader.context
        return self._ctx

    def _guarded(self, kind: str, target: str, call) -> FetchResult:
        """Run one request with pacing; pause and retry once on a refusal; stop on login walls."""
        detail = ""
        for attempt in (1, 2):
            self.pacer.before_request()
            t0 = time.monotonic()
            try:
                data = call()
                outcome, status = Outcome.OK.value, 200
            except Exception as exc:  # noqa: BLE001 - mapped below
                data = None
                outcome, status = _classify_instaloader_error(exc)
                detail = f"{type(exc).__name__}: {str(exc)[:200]}"
            ms = int((time.monotonic() - t0) * 1000)
            if outcome == Outcome.OK.value and kind != "timeline_page" and webparse.is_login_wall(data or []):
                outcome, status = Outcome.LOGIN_WALL.value, 200
            self.recorder("web", kind, target, outcome, status, ms)
            if outcome == Outcome.OK.value:
                self.pacer.ok()
                return FetchResult(outcome, data, status, extra={"raw": data})
            if outcome == Outcome.RATE_LIMITED.value:
                if self.pacer.rate_limited(detail) and attempt == 1:
                    continue
                return FetchResult(outcome, None, status, "rate limited")
            if outcome in (Outcome.LOGIN_WALL.value, Outcome.CHALLENGE.value):
                self.pacer.stop(f"Instagram showed a {outcome.replace('_', ' ')} on {kind}")
                return FetchResult(outcome, None, status, outcome)
            return FetchResult(outcome, None, status, detail)
        return FetchResult(Outcome.RATE_LIMITED.value, None, None, "retry exhausted")

    # ------------------------------------------------------------ pages

    def profile_page(self, username: str) -> FetchResult:
        res = self._guarded("profile_page", username, lambda: self.ctx.get_page_data(f"{username}/"))
        if res.outcome != Outcome.OK.value:
            return res
        raw = {"raw": res.data}
        prof = webparse.parse_profile(res.data, username)
        if prof is None:
            # 200 but no profile data: renamed, deleted or restricted account, or a quiet block.
            # Many in a row is treated as a slow-down signal rather than as many missing accounts.
            self._empty_streak += 1
            if self._empty_streak >= EMPTY_PAGES_BEFORE_BACKOFF:
                self._empty_streak = 0
                self.pacer.rate_limited(f"{EMPTY_PAGES_BEFORE_BACKOFF} profile pages in a row had no data")
            detail = ("page had no profile data (renamed, deleted or restricted account)"
                      if not res.data else "page data present but no profile record (layout change?)")
            return FetchResult(Outcome.NOT_FOUND.value if not res.data else Outcome.ERROR.value, None, 200,
                               detail, extra=raw)
        self._empty_streak = 0
        if prof.get("is_private"):
            return FetchResult(Outcome.PRIVATE.value, prof, 200, extra=raw)
        return FetchResult(Outcome.OK.value, prof, 200, extra=raw)

    def reels_page(self, username: str) -> FetchResult:
        res = self._guarded("reels_page", username, lambda: self.ctx.get_page_data(f"{username}/reels/"))
        if res.outcome != Outcome.OK.value:
            return res
        prof = webparse.parse_profile(res.data, username) or {}
        nodes, info = webparse.reels_nodes(prof.get("clips"))
        return FetchResult(Outcome.OK.value, nodes, 200, extra={"page_info": info, "raw": res.data})

    def timeline_page(self, username: str, page_id: str, after: str) -> FetchResult:
        variables = {"id": page_id, "__relay_internal__pv__PolarisSEOIsCrawlerrelayprovider": False,
                     "after": after, "before": None, "first": 12, "last": None}
        referer = f"https://www.instagram.com/{username}/"

        def call():
            resp = self.ctx.doc_id_graphql_query(TIMELINE_DOC_ID, variables, referer, True)
            return resp["data"]["node"]["polaris_ordered_timeline_connection"]

        res = self._guarded("timeline_page", username, call)
        if res.outcome != Outcome.OK.value:
            return res
        nodes, info = webparse.timeline_nodes(res.data)
        return FetchResult(Outcome.OK.value, nodes, 200, extra={"page_info": info, "raw": res.data})

    def post_page(self, username: str, shortcode: str) -> FetchResult:
        if self._obscura is not None:
            call = lambda: self._obscura.page_data(f"https://www.instagram.com/p/{shortcode}/")  # noqa: E731
        else:
            call = lambda: self.ctx.get_page_data(f"p/{shortcode}/")  # noqa: E731
        res = self._guarded("post_page", shortcode, call)
        if res.outcome != Outcome.OK.value:
            return res
        media = webparse.find_media(res.data, shortcode)
        if media is None:
            return FetchResult(Outcome.NOT_FOUND.value, None, 200, "post data missing from page")
        post, metrics = webparse.parse_post(media)
        return FetchResult(Outcome.OK.value, (post, metrics), 200, extra={"raw": res.data})


class ObscuraFetcher:
    def __init__(self, path: Path | None, sha256: str | None, timeout: int = 20):
        self.path = Path(path) if path else None
        if not self.path or not self.path.is_file():
            raise FileNotFoundError(f"Obscura binary not found at {self.path}. See docs/SETUP.md.")
        if sha256:
            digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
            if digest != sha256:
                raise RuntimeError(f"Obscura binary checksum mismatch ({digest}); expected the pinned 0.2.7 build")
        self.timeout = timeout

    def page_data(self, url: str) -> list[dict]:
        import instaloader.exceptions as ie
        try:
            r = subprocess.run([str(self.path), "fetch", url, "--dump", "original", "--timeout", str(self.timeout)],
                               capture_output=True, text=True, errors="replace", timeout=self.timeout + 5)
        except subprocess.TimeoutExpired as exc:
            raise ie.ConnectionException("obscura timeout") from exc
        if "/accounts/login" in r.stderr:
            raise ie.LoginRequiredException("Redirected to login page (obscura)")
        if not r.stdout.strip():
            # Raw mode hides the status: an empty body is most often a 429/403. Treat it as a refusal.
            raise ie.TooManyRequestsException("empty response body from obscura (status hidden)")
        return page_data(r.stdout)

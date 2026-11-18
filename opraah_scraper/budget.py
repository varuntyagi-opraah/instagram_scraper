"""Request pacing and backoff, one Pacer per source (meta, web).

Policy (deliberately conservative; these are our own limits, not Instagram quotas):
- jittered spacing between requests and an hourly cap;
- when Instagram or Meta pushes back (429, "please wait a few minutes", throttling codes),
  pause 15 → 30 → 60 minutes, then stop that source for the rest of the run;
- a login wall or challenge stops the web source immediately.
Nothing here rotates IPs, spoofs fingerprints or retries harder after a refusal.
"""

from __future__ import annotations

import random
import time
from collections import deque
from enum import Enum
from typing import Callable


class Outcome(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    SKIPPED = "skipped_not_due"
    NOT_FOUND = "not_found"
    PRIVATE = "private"
    UNAVAILABLE = "unavailable"        # meta: not a professional account / age-gated / not found
    RATE_LIMITED = "rate_limited"
    LOGIN_WALL = "login_wall"
    CHALLENGE = "challenge"
    AUTH_ERROR = "auth_error"          # meta token expired / missing permission
    ERROR = "error"


class SourceStopped(Exception):
    def __init__(self, source: str, reason: str):
        super().__init__(f"{source} stopped: {reason}")
        self.source = source
        self.reason = reason


class Pacer:
    def __init__(self, name: str, min_interval: float, max_interval: float, hourly_cap: int,
                 backoff_minutes: tuple[int, ...] = (15, 30, 60), *, wait_on_limit: bool = True,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 rng: Callable[[], float] = random.random, log: Callable[[str], None] = print):
        self.name = name
        self.min_interval = min_interval
        self.max_interval = max_interval
        self.hourly_cap = hourly_cap
        self.backoff_minutes = backoff_minutes
        self.wait_on_limit = wait_on_limit
        self._sleep, self._clock, self._rng, self._log = sleep, clock, rng, log
        self._sent: deque[float] = deque()
        self._next_allowed = 0.0
        self.consecutive_limits = 0
        self.stopped_reason: str | None = None
        self.requests = 0
        self.paused_seconds = 0.0
        self.burst_cap: tuple[int, float] | None = None   # (max requests, window seconds)

    def check(self) -> None:
        if self.stopped_reason:
            raise SourceStopped(self.name, self.stopped_reason)

    def before_request(self) -> None:
        self.check()
        now = self._clock()
        while self._sent and now - self._sent[0] >= 3600:
            self._sent.popleft()
        if self.hourly_cap and len(self._sent) >= self.hourly_cap:
            wait = 3600 - (now - self._sent[0]) + 1
            self._log(f"[{self.name}] hourly cap of {self.hourly_cap} requests reached; waiting {wait / 60:.0f} min")
            self._wait(wait)
            now = self._clock()
        if self.burst_cap:
            n, window = self.burst_cap
            recent = [t for t in self._sent if now - t < window]
            if len(recent) >= n:
                wait = window - (now - recent[0]) + 1
                self._log(f"[{self.name}] {n} requests in {window / 60:.0f} min; waiting {wait / 60:.1f} min")
                self._wait(wait)
                now = self._clock()
        if now < self._next_allowed:
            self._wait(self._next_allowed - now)
        t = self._clock()
        self._sent.append(t)
        self.requests += 1
        self._next_allowed = t + self.min_interval + (self.max_interval - self.min_interval) * self._rng()

    def _wait(self, seconds: float) -> None:
        if seconds > 0:
            self.paused_seconds += seconds
            self._sleep(seconds)

    def ok(self) -> None:
        self.consecutive_limits = 0

    def slow_down(self, seconds: float, why: str) -> None:
        self._log(f"[{self.name}] {why}; waiting {seconds:.0f}s")
        self._wait(seconds)

    def rate_limited(self, detail: str = "") -> bool:
        """Back off after a refusal. True = paused and the caller may retry once; False = stopped."""
        self.consecutive_limits += 1
        k = self.consecutive_limits
        if not self.wait_on_limit or k > len(self.backoff_minutes):
            self.stop(f"rate limited {k}x{(': ' + detail) if detail else ''}")
            return False
        minutes = self.backoff_minutes[k - 1]
        self._log(f"[{self.name}] asked to slow down ({detail or 'rate limited'}); pausing {minutes} min. "
                  "Progress is saved, Ctrl-C is safe.")
        self._wait(minutes * 60)
        return True

    def stop(self, reason: str) -> None:
        if not self.stopped_reason:
            self._log(f"[{self.name}] stopping for this run: {reason}")
        self.stopped_reason = reason

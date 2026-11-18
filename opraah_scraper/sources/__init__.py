"""Data sources. Each returns normalized dicts plus an Outcome; none writes to the DB."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

# recorder(source, kind, target, outcome, http_status, ms)
Recorder = Callable[[str, str, str, str, "int | None", "int | None"], None]


def null_recorder(*_args) -> None:
    return None


@dataclass
class FetchResult:
    outcome: str
    data: object = None
    http_status: int | None = None
    detail: str = ""
    extra: dict = field(default_factory=dict)

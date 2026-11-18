"""Read roster exports or plain username lists and normalize Instagram handles.

Accepted inputs:
- A CSV with an `Instagram` / `username` / `handle` column (profile URL, @handle or bare
  handle), optionally `Vendor Code`, `Type` (Exclusive / Non-Exclusive) and any other
  roster columns, which are kept verbatim.
- A text file with one handle or profile URL per line (`#` starts a comment).

Two vendor records that share a handle are never merged: each keeps its own row and both
link to the one Instagram account, which is fetched once.
"""

from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

HANDLE_COLUMNS = ("instagram", "username", "instagram username", "instagram handle",
                  "instagram url", "instagram_url", "handle", "ig", "ig handle")
VENDOR_COLUMNS = ("vendor code", "vendor_code", "vendorcode")
PLACEHOLDERS = {"", "na", "n/a", "none", "nil", "null", "-", "--", "0", "nan", "no", "not available"}
RESERVED_PATHS = {"p", "reel", "reels", "tv", "stories", "explore", "accounts", "direct",
                  "about", "legal", "developer", "web", "s", "share"}
_HANDLE_RE = re.compile(r"^[a-z0-9._]{1,30}$")
_URL_RE = re.compile(r"(?:https?://)?(?:[a-z0-9-]+\.)*instagram\.com/(?:_u/)?([^/?#\s]+)", re.I)


def normalize_handle(value: str | None) -> tuple[str | None, str | None]:
    """Return (handle, problem). A missing value gives (None, None); a bad one gives (None, reason)."""
    v = (value or "").strip()
    if v.lower() in PLACEHOLDERS:
        return None, None
    if "instagram.com" in v.lower():
        m = _URL_RE.search(v)
        if not m:
            return None, "unparseable_instagram_url"
        candidate = m.group(1)
        if candidate.lower() in RESERVED_PATHS:
            return None, "post_or_non_profile_url"
    elif re.match(r"^(?:https?://|www\.)", v, re.I):
        return None, "not_an_instagram_url"
    else:
        # A name such as "Ravi Kumar" in the Instagram column is not a handle.
        candidate = v.split("?")[0]
        if re.search(r"\s", candidate):
            return None, "invalid_handle_characters"
    candidate = candidate.strip().lstrip("@").strip("/").lower()
    if not _HANDLE_RE.match(candidate) or ".." in candidate or candidate.strip(".") == "":
        return None, "invalid_handle_characters"
    return candidate, None


YOUTUBE_COLUMNS = ("youtube", "youtube url", "youtube_url", "youtube handle", "youtube channel")
_YT_RE = re.compile(r"(?:https?://)?(?:www\.|m\.)?youtube\.com/(@[A-Za-z0-9._-]+|channel/(UC[A-Za-z0-9_-]{22})|"
                    r"user/([A-Za-z0-9._-]+)|c/([^/?#\s]+))", re.I)


def normalize_youtube(value: str | None) -> str | None:
    """'@handle' | 'channel:UC…' | 'user:name' | 'c:name', or None."""
    v = (value or "").strip()
    if v.lower() in PLACEHOLDERS:
        return None
    m = _YT_RE.search(v)
    if m:
        if m.group(2):
            return f"channel:{m.group(2)}"
        if m.group(3):
            return f"user:{m.group(3).lower()}"
        if m.group(4):
            return f"c:{m.group(4).lower()}"
        return m.group(1).lower()
    if re.fullmatch(r"@[A-Za-z0-9._-]{3,30}", v):
        return v.lower()
    if re.fullmatch(r"UC[A-Za-z0-9_-]{22}", v):
        return f"channel:{v}"
    return None


def _tier_from(value: str | None, source_file: str) -> str:
    v = (value or "").strip().lower().replace("_", "-")
    if v in {"exclusive"}:
        return "exclusive"
    if v in {"non-exclusive", "nonexclusive", "non exclusive"}:
        return "non_exclusive"
    if v.startswith("agency"):
        return "agency_talent"
    if v.startswith("profile only"):
        return "profile_only"
    name = Path(source_file).name.lower()
    if "non-exclusive" in name or "non_exclusive" in name or "nonexclusive" in name:
        return "non_exclusive"
    if "exclusive" in name:
        return "exclusive"
    return ""


@dataclass
class RosterRow:
    source_file: str
    line_no: int
    vendor_code: str  # "" when the input has no vendor code (plain username lists)
    handle: str | None
    problem: str | None
    instagram_raw: str
    tier: str
    name: str = ""
    team: str = ""
    category: str = ""
    location: str = ""
    gender: str = ""
    exec_poc: str = ""
    roster_followers: int | None = None
    roster_avg_views: int | None = None
    roster_er: float | None = None
    youtube_raw: str = ""
    youtube_ref: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class RosterImport:
    rows: list[RosterRow]

    @property
    def with_handle(self) -> list[RosterRow]:
        return [r for r in self.rows if r.handle]

    @property
    def missing(self) -> list[RosterRow]:
        return [r for r in self.rows if not r.handle and not r.problem]

    @property
    def invalid(self) -> list[RosterRow]:
        return [r for r in self.rows if r.problem]

    @property
    def handles(self) -> list[str]:
        """Distinct handles in first-seen order: the fetch list."""
        return list(dict.fromkeys(r.handle for r in self.rows if r.handle))

    @property
    def shared_handles(self) -> dict[str, list[str]]:
        by: dict[str, set[str]] = defaultdict(set)
        for r in self.with_handle:
            if r.vendor_code:
                by[r.handle].add(r.vendor_code)
        return {h: sorted(v) for h, v in by.items() if len(v) > 1}

    @property
    def both_tiers(self) -> list[str]:
        by: dict[str, set[str]] = defaultdict(set)
        for r in self.with_handle:
            if r.tier:
                by[r.handle].add(r.tier)
        return sorted(h for h, v in by.items() if len(v) > 1)

    @property
    def duplicate_vendor_codes(self) -> list[str]:
        c = Counter(r.vendor_code for r in self.rows if r.vendor_code)
        return sorted(v for v, n in c.items() if n > 1)

    def summary(self) -> dict:
        tiers = Counter(r.tier or "unknown" for r in self.rows)
        return {
            "rows": len(self.rows),
            "by_tier": dict(tiers),
            "rows_with_handle": len(self.with_handle),
            "rows_without_handle": len(self.missing),
            "rows_with_youtube": sum(1 for r in self.rows if r.youtube_ref),
            "rows_with_invalid_handle": len(self.invalid),
            "distinct_handles": len(self.handles),
            "handles_shared_by_vendor_codes": len(self.shared_handles),
            "handles_in_both_tiers": len(self.both_tiers),
            "duplicate_vendor_codes": len(self.duplicate_vendor_codes),
        }

    def review_rows(self) -> list[dict]:
        shared = self.shared_handles
        both = set(self.both_tiers)
        out = []
        for r in self.rows:
            issues = []
            if r.problem:
                issues.append(r.problem)
            elif not r.handle:
                issues.append("missing_instagram")
            if r.handle in shared:
                others = [v for v in shared[r.handle] if v != r.vendor_code]
                issues.append("shared_handle_with:" + "|".join(others))
            if r.handle in both:
                issues.append("handle_in_both_tiers")
            if issues:
                out.append({"source_file": Path(r.source_file).name, "line": r.line_no,
                            "vendor_code": r.vendor_code, "name": r.name, "tier": r.tier,
                            "instagram_value": r.instagram_raw, "handle": r.handle or "",
                            "issues": ";".join(issues)})
        return out


def _num(value: str | None, cast):
    v = (value or "").strip().replace(",", "")
    if not v:
        return None
    try:
        return cast(float(v)) if cast is int else cast(v)
    except ValueError:
        return None


def _pick(row: dict[str, str], names: tuple[str, ...]) -> str | None:
    lowered = {(k or "").strip().lower(): k for k in row}
    for n in names:
        if n in lowered:
            return row[lowered[n]]
    return None


def read_csv(path: Path) -> list[RosterRow]:
    rows: list[RosterRow] = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            return rows
        lowered = [(f or "").strip().lower() for f in reader.fieldnames]
        if not any(n in lowered for n in HANDLE_COLUMNS + YOUTUBE_COLUMNS):
            raise ValueError(f"{path.name}: no Instagram/username column (columns: {reader.fieldnames})")
        for i, raw in enumerate(reader, start=2):
            value = _pick(raw, HANDLE_COLUMNS) or ""
            handle, problem = normalize_handle(value)
            yt_raw = (_pick(raw, YOUTUBE_COLUMNS) or "").strip()
            rows.append(RosterRow(
                source_file=str(path), line_no=i,
                vendor_code=(_pick(raw, VENDOR_COLUMNS) or "").strip(),
                handle=handle, problem=problem, instagram_raw=value,
                tier=_tier_from(_pick(raw, ("type", "tier", "roster", "roster type")), str(path)),
                youtube_raw=yt_raw, youtube_ref=normalize_youtube(yt_raw),
                name=(_pick(raw, ("creator name", "name")) or "").strip(),
                team=(_pick(raw, ("team",)) or "").strip(),
                category=(_pick(raw, ("creator category", "category", "categories")) or "").strip(),
                location=(_pick(raw, ("location", "city", "location (raw)")) or "").strip(),
                gender=(_pick(raw, ("gender",)) or "").strip(),
                exec_poc=(_pick(raw, ("exec poc",)) or "").strip(),
                roster_followers=_num(_pick(raw, ("instagram followers", "ig followers")), int),
                roster_avg_views=_num(_pick(raw, ("avg ig views", "ig avg views")), int),
                roster_er=_num(_pick(raw, ("ig er (%)", "ig er %")), float),
                raw={k: v for k, v in raw.items() if k is not None},
            ))
    return rows


def read_text(path: Path) -> list[RosterRow]:
    rows = []
    for i, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        value = line.split("#", 1)[0].strip()
        if not value:
            continue
        handle, problem = normalize_handle(value)
        rows.append(RosterRow(source_file=str(path), line_no=i, vendor_code="", handle=handle,
                              problem=problem or (None if handle else "invalid_handle_characters"),
                              instagram_raw=value, tier=_tier_from(None, str(path))))
    return rows


def load_inputs(paths: list[Path]) -> RosterImport:
    rows: list[RosterRow] = []
    for p in paths:
        p = Path(p).expanduser()
        if p.suffix.lower() == ".csv":
            rows.extend(read_csv(p))
        else:
            rows.extend(read_text(p))
    return RosterImport(rows)


def write_review_csv(imp: RosterImport, path: Path) -> int:
    rows = imp.review_rows()
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["source_file", "line", "vendor_code", "name", "tier", "instagram_value", "handle", "issues"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return len(rows)

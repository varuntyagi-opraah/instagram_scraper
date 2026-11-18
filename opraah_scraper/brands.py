"""Brand evidence rules (v1: local rules, no LLM).

Every edge names its post, the exact text or metadata that supports it, and a rule id.
No rule infers payment. Ambiguous edges are marked needs_review for a human.

Kinds:
  coauthor                   brand/creator listed in the post's co-author field
  collab_post_owned_by_brand post published by another account and shown on/co-authored by the creator
  collaboration_disclosed    caption carries a disclosure (#ad, #collab, "in partnership with", ...)
                             or Instagram's paid-partnership label/sponsor tag (when observed)
  product_integration        promo language (use code, link in bio, % off, ...) next to a mention
  event_association          event language next to a mention
  mention                    any other @mention or tagged account
  hashtag                    hashtag that matches a registered brand alias (e.g. #porsche)
  creator_collab             the counterpart is another creator on the Opraah roster
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import PROJECT_ROOT

DISCLOSURE_RE = re.compile(
    r"(#(?:ad|ads|advertisement|sponsored|sponsor|collab|collaboration|paidpartnership|partnership|"
    r"brandpartner|ambassador|promo|giveaway)\b|\bpaid partnership\b|\bin (?:partnership|collaboration|association) with\b|"
    r"\bsponsored by\b|\bpowered by\b|\bpresented by\b|\bin collab with\b)", re.I)
PROMO_RE = re.compile(
    r"(\buse (?:my )?code\b|\bcoupon\b|\bpromo ?code\b|\bdiscount\b|\b\d{1,2}\s?% off\b|\blink in (?:my )?bio\b|"
    r"\bshop now\b|\border now\b|\bbuy (?:now|yours)\b|\bdownload (?:now|the app)\b|\bavailable (?:on|at)\b|"
    r"\bat (?:a )?great price\b|\bper month\b|₹\s?\d|\binr \d)", re.I)
EVENT_RE = re.compile(r"(\bevent\b|\blaunch\b|\bexpo\b|\bsummit\b|\bpremiere\b|\bscreening\b|\btournament\b|"
                      r"\bconvention\b|\bcon\b|\bmeet ?(?:and|&) ?greet\b|\bthank you for having me\b|\bfest\b)", re.I)
REVIEWED_KINDS = {"product_integration", "event_association"}


@dataclass
class Registry:
    brands: dict[str, dict] = field(default_factory=dict)  # handle -> {name, category}
    exact_alias: dict[str, str] = field(default_factory=dict)  # hashtag -> handle
    prefix_alias: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None = None) -> "Registry":
        path = path or PROJECT_ROOT / "config" / "brands.csv"
        reg = cls()
        if not Path(path).is_file():
            return reg
        with Path(path).open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                handle = (row.get("handle") or "").strip().lower().lstrip("@")
                if not handle:
                    continue
                reg.brands[handle] = {"name": (row.get("name") or handle).strip(),
                                      "category": (row.get("category") or "").strip()}
                for alias in (row.get("aliases") or "").split("|"):
                    a = alias.strip().lower().lstrip("#")
                    if not a:
                        continue
                    if a.endswith("*"):
                        reg.prefix_alias.append((a[:-1], handle))
                    else:
                        reg.exact_alias[a] = handle
                reg.exact_alias.setdefault(handle, handle)
        reg.prefix_alias.sort(key=lambda t: -len(t[0]))
        return reg

    def brand_for_hashtag(self, tag: str) -> str | None:
        t = tag.lower()
        if t in self.exact_alias:
            return self.exact_alias[t]
        for prefix, handle in self.prefix_alias:
            if t.startswith(prefix):
                return handle
        return None


@dataclass
class Edge:
    brand_handle: str
    kind: str
    evidence_text: str
    rule_id: str
    review_status: str = "auto"


def _snippet(caption: str, match: re.Match | None, width: int = 90) -> str:
    if not caption:
        return ""
    if match is None:
        return caption[:width * 2].replace("\n", " ").strip()
    start = max(0, match.start() - width)
    return caption[start:match.end() + width].replace("\n", " ").strip()


def derive_edges(post: dict, account_username: str, registry: Registry, roster_handles: set[str]) -> list[Edge]:
    """Brand/creator edges for one post as seen from one account."""
    me = account_username.lower()
    caption = post.get("caption") or ""
    owner = (post.get("owner_username") or "").lower()
    coauthors = [c for c in (post.get("coauthors") or []) if c and c.lower() != me]
    mentions = [m for m in (post.get("mentions") or []) if m and m.lower() != me]
    usertags = [u for u in (post.get("usertags") or []) if u and u.lower() != me]
    sponsors = [s for s in (post.get("sponsors") or []) if s and s.lower() != me]
    hashtags = post.get("hashtags") or []
    edges: list[Edge] = []

    def kind_for(handle: str, default: str) -> str:
        return "creator_collab" if handle in roster_handles else default

    disclosure = DISCLOSURE_RE.search(caption)
    promo = PROMO_RE.search(caption)
    event = EVENT_RE.search(caption)

    if owner and owner != me:
        edges.append(Edge(owner, kind_for(owner, "collab_post_owned_by_brand"),
                          f"Post published by @{owner}" + (f"; co-authors: {', '.join('@' + c for c in post.get('coauthors') or [])}"
                                                           if post.get("coauthors") else "; shown on this profile"),
                          "owner_is_other_account"))
    for c in coauthors:
        if c == owner:
            continue
        edges.append(Edge(c, kind_for(c, "coauthor"), f"Co-author field lists @{c}", "coauthor_field"))
    for s in sponsors:
        edges.append(Edge(s, kind_for(s, "collaboration_disclosed"), f"Paid partnership label / sponsor tag: @{s}",
                          "paid_partnership_label"))
    if post.get("is_paid_partnership") and not sponsors:
        edges.append(Edge("(unnamed)", "collaboration_disclosed", "Instagram paid-partnership label without sponsor tag",
                          "paid_partnership_label_unnamed", "needs_review"))

    named = list(dict.fromkeys([*coauthors, *mentions, *usertags]))
    brand_tags = list(dict.fromkeys(h for h in (registry.brand_for_hashtag(t) for t in hashtags) if h))

    if disclosure:
        text = _snippet(caption, disclosure)
        targets = [h for h in named if h not in roster_handles] or [h for h in brand_tags if h != me]
        for h in targets:
            edges.append(Edge(h, "collaboration_disclosed", text, "caption_disclosure"))
        for h in named:
            if h in roster_handles:
                edges.append(Edge(h, "creator_collab", text, "caption_disclosure"))
        if not targets and not any(h in roster_handles for h in named):
            edges.append(Edge("(unnamed)", "collaboration_disclosed", text, "caption_disclosure_unnamed", "needs_review"))

    for h in mentions:
        if promo:
            edges.append(Edge(h, kind_for(h, "product_integration"), _snippet(caption, promo), "promo_language_with_mention",
                              "auto" if disclosure else "needs_review"))
        elif event:
            edges.append(Edge(h, kind_for(h, "event_association"), _snippet(caption, event), "event_language_with_mention",
                              "needs_review"))
        else:
            m = re.search("@" + re.escape(h), caption, re.I)
            edges.append(Edge(h, kind_for(h, "mention"), _snippet(caption, m, 60), "caption_mention"))
    for u in usertags:
        if u not in mentions:
            edges.append(Edge(u, kind_for(u, "mention"), f"Tagged in media: @{u}", "media_usertag"))
    for t in hashtags:
        h = registry.brand_for_hashtag(t)
        if h and h != me:
            edges.append(Edge(h, "hashtag", f"#{t}", "registered_brand_hashtag"))

    # One edge per (handle, kind); a plain mention/hashtag is dropped when the same post already
    # gives that handle a stronger edge (co-author, disclosure, integration, ...).
    strong = {e.brand_handle for e in edges if e.kind not in ("mention", "hashtag")}
    seen, out = set(), []
    for e in edges:
        key = (e.brand_handle, e.kind)
        if key in seen or (e.kind in ("mention", "hashtag") and e.brand_handle in strong):
            continue
        seen.add(key)
        out.append(e)
    return out


BRAND_WORK_KINDS = ("collaboration_disclosed", "coauthor", "collab_post_owned_by_brand", "product_integration")

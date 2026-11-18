"""Normalize logged-out Instagram page data (profile, timeline, Reels tab, post page).

Input is the list of embedded GraphQL `data` blocks, as returned by
InstaloaderContext.get_page_data() or parse.embedded_json.page_data(html).
"""

from __future__ import annotations

import re
from typing import Any

from ..util import from_epoch, int_or_none
from .embedded_json import all_dicts
from .text import date_from_accessibility_caption, hashtags, mentions

_SHORTCODE = re.compile(r"^[A-Za-z0-9_-]{5,}$")
LOGIN_WALL_KEYS = ("caa_login_form_data", "caa_feta_logged_out_homepage_root")


def is_login_wall(blocks: list[dict]) -> bool:
    """The page Instagram serves instead of content when it wants a login."""
    has_wall = any(k in b for b in blocks for k in LOGIN_WALL_KEYS)
    has_user = any("xig_user_by_username" in b or "xig_polaris_media" in b for b in blocks)
    return has_wall and not has_user


def _caption_text(node: dict) -> str | None:
    cap = node.get("caption")
    if isinstance(cap, dict):
        return cap.get("text")
    if isinstance(cap, str):
        return cap
    return None


def media_format(product_type: str | None, media_type: Any) -> str | None:
    if (product_type or "").lower() == "clips":
        return "reel"
    return {1: "image", 2: "video", 8: "carousel"}.get(media_type)


def _usernames(items) -> list[str]:
    out = []
    for x in items or []:
        if not isinstance(x, dict):
            continue
        u = (x.get("sponsor") or x.get("user") or x).get("username")
        if u and u.lower() not in out:
            out.append(u.lower())
    return out


def _user_records(blocks: list[dict], username: str) -> list[dict]:
    out = []
    for b in blocks:
        u = b.get("xig_user_by_username")
        if isinstance(u, dict):
            out.append(u)
    return out


def parse_profile(blocks: list[dict], username: str) -> dict | None:
    """Profile fields plus the first timeline connection, or None if absent."""
    users = _user_records(blocks, username)
    if not users:
        return None
    merged: dict = {}
    for u in users:
        for k, v in u.items():
            if merged.get(k) is None:
                merged[k] = v
    name = (merged.get("username") or username).lower()
    links = []
    for link in merged.get("bio_links") or []:
        if isinstance(link, dict) and link.get("url"):
            links.append({"url": link.get("url"), "title": link.get("title") or ""})
    return {
        "username": name,
        "ig_pk": str(merged["pk"]) if merged.get("pk") else None,
        "graph_id": str(merged["id"]) if merged.get("id") else None,  # page "id" == Graph API IG User id
        "followers": int_or_none(merged.get("follower_count")),
        "following": int_or_none(merged.get("following_count")),
        "media_count": int_or_none(merged.get("all_media_count")),
        "full_name": merged.get("full_name"),
        "biography": merged.get("biography"),
        "bio_links": links,
        "website": links[0]["url"] if links else None,
        "is_verified": merged.get("is_verified"),
        "is_private": merged.get("is_private"),
        "profile_pic_url": merged.get("profile_pic_url"),
        "timeline": merged.get("polaris_ordered_timeline_connection"),
        "clips": merged.get("polaris_clips_connection"),
    }


def timeline_nodes(connection: dict | None) -> tuple[list[dict], dict]:
    """Thin posts from a profile timeline connection (no counts, day-precision dates)."""
    if not connection:
        return [], {"has_next_page": False, "end_cursor": None}
    out = []
    for edge in connection.get("edges") or []:
        n = edge.get("node") if isinstance(edge, dict) else None
        if not isinstance(n, dict) or not _SHORTCODE.match(str(n.get("code") or "")):
            continue
        owner = n.get("user") or {}
        caption = _caption_text(n)
        a11y = n.get("accessibility_caption")
        out.append({
            "shortcode": n["code"],
            "media_pk": str(n.get("pk")) if n.get("pk") else None,
            "owner_username": (owner.get("username") or "").lower() or None,
            "owner_graph_id": str(owner.get("id")) if owner.get("id") else None,
            "format": media_format(n.get("product_type"), n.get("media_type")),
            "product_type": n.get("product_type"),
            "caption": caption,
            "hashtags": hashtags(caption),
            "mentions": mentions(caption),
            "accessibility_caption": a11y,
            "approx_date": date_from_accessibility_caption(a11y),
        })
    return out, dict(connection.get("page_info") or {})


def reels_nodes(connection: dict | None) -> tuple[list[dict], dict]:
    """Reels tab entries: counts and plays, no captions or dates."""
    if not connection:
        return [], {"has_next_page": False, "end_cursor": None}
    out = []
    for edge in connection.get("edges") or []:
        n = edge.get("node") if isinstance(edge, dict) else None
        if not isinstance(n, dict) or not _SHORTCODE.match(str(n.get("code") or "")):
            continue
        hidden = bool(n.get("like_and_view_counts_disabled"))
        owner = n.get("user") or {}
        out.append({
            "shortcode": n["code"],
            "media_pk": str(n.get("pk")) if n.get("pk") else None,
            "owner_graph_id": str(owner.get("id")) if owner.get("id") else None,
            "owner_pk": str(owner.get("pk")) if owner.get("pk") else None,
            "format": "reel",
            "likes": None if hidden else int_or_none(n.get("like_count")),
            "comments": int_or_none(n.get("comment_count")),
            "plays": None if hidden else int_or_none(n.get("play_count")),
            "metrics_hidden": hidden,
            "pinned_in_reels_tab": bool(n.get("clips_tab_pinned_user_ids")),
        })
    return out, dict(connection.get("page_info") or {})


def _audio(media: dict) -> dict | None:
    cm = media.get("clips_metadata")
    if not isinstance(cm, dict):
        return None
    music = (cm.get("music_info") or {}).get("music_asset_info") if isinstance(cm.get("music_info"), dict) else None
    if isinstance(music, dict):
        return {"title": music.get("title"), "artist": music.get("display_artist"), "original": False}
    osi = cm.get("original_sound_info")
    if isinstance(osi, dict):
        return {"title": osi.get("original_audio_title"),
                "artist": ((osi.get("ig_artist") or {}).get("username") or "").lower() or None,
                "original": True}
    return None


def find_media(blocks: list[dict], shortcode: str) -> dict | None:
    for d in all_dicts(blocks):
        if (d.get("code") or d.get("shortcode")) == shortcode and isinstance(d.get("taken_at"), int) \
                and isinstance(d.get("user") or d.get("owner"), dict):
            return d
    return None


def parse_post(media: dict) -> tuple[dict, dict]:
    """(post fields, metrics) from a post page media dict."""
    owner = media.get("user") or media.get("owner") or {}
    caption = _caption_text(media)
    loc = media.get("location")
    hidden = bool(media.get("like_and_view_counts_disabled"))
    usertags = _usernames(((media.get("usertags") or {}).get("in")) if isinstance(media.get("usertags"), dict) else [])
    post = {
        "shortcode": media.get("code") or media.get("shortcode"),
        "media_pk": str(media.get("pk")) if media.get("pk") else None,
        "owner_username": (owner.get("username") or "").lower() or None,
        "owner_pk": str(owner.get("pk") or owner.get("id") or "") or None,
        "taken_at": from_epoch(media.get("taken_at")),
        "format": media_format(media.get("product_type"), media.get("media_type")),
        "product_type": media.get("product_type"),
        "caption": caption,
        "hashtags": hashtags(caption),
        "mentions": mentions(caption),
        "coauthors": _usernames(media.get("coauthor_producers")) if "coauthor_producers" in media else None,
        "usertags": usertags,
        "location_name": loc.get("name") if isinstance(loc, dict) else None,
        "audio": _audio(media),
        "accessibility_caption": media.get("accessibility_caption"),
        "metrics_hidden": hidden,
        # Logged-out pages normally omit these; kept when present.
        "is_paid_partnership": media.get("is_paid_partnership"),
        "sponsors": _usernames(media.get("sponsor_tags")) if media.get("sponsor_tags") else None,
    }
    code = post["shortcode"]
    post["permalink"] = f"https://www.instagram.com/{'reel' if post['format'] == 'reel' else 'p'}/{code}/"
    plays = next((media[k] for k in ("play_count", "ig_play_count", "view_count", "video_view_count")
                  if int_or_none(media.get(k)) is not None), None)
    metrics = {
        "likes": None if hidden else int_or_none(media.get("like_count")),
        "comments": int_or_none(media.get("comment_count")),
        "plays": None if hidden else plays,
    }
    return post, metrics

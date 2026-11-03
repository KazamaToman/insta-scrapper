"""Turn raw Instagram payloads into :class:`~instascrapper.models.Post` objects.

Two payload families are supported:

* ``web_profile_info`` nodes (``edge_owner_to_timeline_media.edges[].node``)
* ``api/v1`` feed items (``/api/v1/feed/user/<pk>/``)

Both are handled defensively - Instagram adds/renames fields regularly, so every
lookup has a fallback.
"""

from __future__ import annotations

from typing import Any, Iterable

from .models import MediaItem, Post, Profile, _clean_url


# --------------------------------------------------------------------------- utils
def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _first(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return default


def _best_candidate(candidates: Iterable[dict] | None) -> dict | None:
    """Pick the highest-resolution URL from an ``image_versions2``/``video_versions`` list."""
    best: dict | None = None
    best_area = -1
    for candidate in candidates or []:
        if not isinstance(candidate, dict) or not candidate.get("url"):
            continue
        area = _as_int(candidate.get("width")) * _as_int(candidate.get("height"))
        if area > best_area:
            best, best_area = candidate, area
    return best


def _best_from_candidates(candidates: Iterable[dict] | None, media_type: str, index: int) -> MediaItem | None:
    best = _best_candidate(candidates)
    if not best:
        return None
    return MediaItem(
        index=index,
        media_type=media_type,
        url=_clean_url(best["url"]),
        width=_as_int(best.get("width")),
        height=_as_int(best.get("height")),
    )


def _unique(items: Iterable[MediaItem]) -> list[MediaItem]:
    seen: set[str] = set()
    result: list[MediaItem] = []
    for item in items:
        key = item.url.split("?")[0]
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    for position, item in enumerate(result, start=1):
        item.index = position
    return result


# ------------------------------------------------------------------- web_profile_info
def parse_graph_media(node: dict) -> Post:
    """Parse a node from ``edge_owner_to_timeline_media`` (or a sidecar child)."""
    typename = str(node.get("__typename") or "")
    is_video = bool(node.get("is_video")) or typename in {"GraphVideo", "XDTGraphVideo"}
    media: list[MediaItem] = []
    dimensions = node.get("dimensions") or {}

    children = (
        (node.get("edge_sidecar_to_children") or {}).get("edges")
        or node.get("carousel_media")
        or []
    )
    child_posts: list[Post] = []
    for edge in children:
        child = edge.get("node") if isinstance(edge, dict) and "node" in edge else edge
        if isinstance(child, dict):
            child_posts.append(parse_graph_media(child))

    if child_posts:
        # Sidecar: the node's own display_url is just the cover of slide 1.
        for child in child_posts:
            media.extend(child.media)
    elif is_video and node.get("video_url"):
        media.append(
            MediaItem(
                index=1,
                media_type="video",
                url=_clean_url(node["video_url"]),
                width=_as_int(dimensions.get("width")),
                height=_as_int(dimensions.get("height")),
            )
        )
    elif node.get("display_url"):
        media.append(
            MediaItem(
                index=1,
                media_type="image",
                url=_clean_url(node["display_url"]),
                width=_as_int(dimensions.get("width")),
                height=_as_int(dimensions.get("height")),
            )
        )

    caption = _first(
        _nested(node, "edge_media_to_caption", "edges", 0, "node", "text"),
        node.get("caption"),
        _first(node.get("title")),
        default="",
    )
    if isinstance(caption, dict):
        caption = caption.get("text", "")

    like_count = _as_int(
        _first(
            _nested(node, "edge_media_preview_like", "count"),
            _nested(node, "edge_liked_by", "count"),
            node.get("like_count"),
            default=0,
        )
    )
    comment_count = _as_int(
        _first(_nested(node, "edge_media_to_comment", "count"), node.get("comment_count"), default=0)
    )

    media = _unique(media)
    if child_posts:
        media_type = "carousel"
    elif is_video:
        media_type = "video"
    else:
        media_type = "image"

    return Post(
        shortcode=str(node.get("shortcode") or node.get("code") or ""),
        id=str(node.get("id") or ""),
        taken_at=_as_int(_first(node.get("taken_at_timestamp"), node.get("taken_at"), default=0)),
        caption=str(caption)[:4000],
        like_count=like_count,
        comment_count=comment_count,
        media_type=media_type,
        owner_username=str(_nested(node, "owner", "username") or ""),
        media=media,
        accessibility_caption=str(node.get("accessibility_caption") or ""),
        thumbnail_url=_clean_url(node.get("display_url") or (media[0].url if media else "")),
        width=_as_int(dimensions.get("width")),
        height=_as_int(dimensions.get("height")),
    )


def _nested(obj: Any, *path: Any) -> Any:
    """Safe deep lookup: ``_nested(d, "a", 0, "b")``."""
    current = obj
    for key in path:
        try:
            if isinstance(key, int):
                current = current[key]
            elif isinstance(current, dict):
                current = current.get(key)
            else:
                return None
        except (IndexError, KeyError, TypeError):
            return None
        if current is None:
            return None
    return current


# ------------------------------------------------------------------------ feed items
def parse_feed_item(item: dict) -> Post:
    """Parse an item from ``/api/v1/feed/user/<pk>/``."""
    media_type_code = _as_int(item.get("media_type"), 1)
    media: list[MediaItem] = []

    if media_type_code == 8 or item.get("carousel_media"):
        for position, child in enumerate(item.get("carousel_media") or [], start=1):
            media.extend(_media_from_feed_block(child, position))
    else:
        media.extend(_media_from_feed_block(item, 1))

    caption = _nested(item, "caption", "text") or (item.get("caption") if isinstance(item.get("caption"), str) else "")
    media = _unique(media)
    if media_type_code == 8:
        resolved_type = "carousel"
    elif media_type_code == 2:
        resolved_type = "video"
    else:
        resolved_type = "image"

    return Post(
        shortcode=str(item.get("code") or item.get("shortcode") or ""),
        id=str(item.get("id") or item.get("pk") or ""),
        taken_at=_as_int(item.get("taken_at") or item.get("taken_at_timestamp")),
        caption=str(caption or "")[:4000],
        like_count=_as_int(item.get("like_count")),
        comment_count=_as_int(item.get("comment_count")),
        media_type=resolved_type,
        owner_username=str(_nested(item, "user", "username") or ""),
        media=media,
        accessibility_caption=str(item.get("accessibility_caption") or ""),
        thumbnail_url=_thumbnail(item),
        width=_as_int(item.get("original_width")),
        height=_as_int(item.get("original_height")),
    )


def _thumbnail(item: dict) -> str:
    """Cover image for a feed item (video posts still ship a poster frame)."""
    best = _best_candidate(_nested(item, "image_versions2", "candidates"))
    if best:
        return _clean_url(best["url"])
    for child in item.get("carousel_media") or []:
        best = _best_candidate(_nested(child, "image_versions2", "candidates"))
        if best:
            return _clean_url(best["url"])
    return ""


def _media_from_feed_block(block: dict, position: int) -> list[MediaItem]:
    if not isinstance(block, dict):
        return []
    results: list[MediaItem] = []
    video = _best_from_candidates(block.get("video_versions"), "video", position)
    if video:
        results.append(video)
    image = _best_from_candidates(_nested(block, "image_versions2", "candidates"), "image", position)
    if image:
        results.append(image)
    # Video posts still ship a poster image; we only want the video file itself.
    if video:
        results = [video]
    return results


# --------------------------------------------------------------------------- profile
def parse_profile(payload: dict) -> Profile:
    """Parse ``/api/v1/users/web_profile_info/?username=...``."""
    user = _nested(payload, "data", "user") or payload.get("user") or payload
    if not isinstance(user, dict) or not user.get("username"):
        raise ValueError("unexpected profile payload")

    edges = _nested(user, "edge_owner_to_timeline_media", "edges") or []
    posts = [parse_graph_media(edge["node"]) for edge in edges if isinstance(edge, dict) and edge.get("node")]
    page_info = _nested(user, "edge_owner_to_timeline_media", "page_info") or {}

    return Profile(
        id=str(user.get("id") or user.get("pk") or ""),
        username=str(user.get("username")),
        full_name=str(user.get("full_name") or ""),
        biography=str(user.get("biography") or ""),
        followers=_as_int(_first(_nested(user, "edge_followed_by", "count"), user.get("follower_count"), default=0)),
        following=_as_int(_first(_nested(user, "edge_follow", "count"), user.get("following_count"), default=0)),
        posts_count=_as_int(
            _first(_nested(user, "edge_owner_to_timeline_media", "count"), user.get("media_count"), default=len(posts))
        ),
        is_private=bool(user.get("is_private")),
        is_verified=bool(user.get("is_verified")),
        profile_pic_url=_clean_url(
            _first(user.get("profile_pic_url_hd"), user.get("profile_pic_url"), default="")
        ),
        external_url=str(_first(user.get("external_url"), _nested(user, "bio_links", 0, "url"), default="") or ""),
        posts=posts,
        has_next_page=bool(page_info.get("has_next_page")),
        end_cursor=page_info.get("end_cursor"),
    )

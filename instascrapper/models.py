"""Normalised data model shared by the API client, downloader, CLI and web UI.

Instagram's two public JSON shapes (the ``web_profile_info`` GraphQL-ish payload
and the ``api/v1`` mobile feed payload) are both mapped onto these classes, so
downstream code never has to care which endpoint answered.
"""

from __future__ import annotations

import datetime as _dt
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Iterable

_EXT_RE = re.compile(r"\.(jpe?g|png|webp|mp4|mov|m4v|heic)$", re.IGNORECASE)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}


def _clean_url(url: str | None) -> str:
    if not url:
        return ""
    url = url.replace("\\u0026", "&").replace("&amp;", "&")
    if url.startswith("//"):
        url = "https:" + url
    return url


def extension_for(url: str, media_type: str = "image") -> str:
    """Best-effort file extension for a CDN URL."""
    path = urllib.parse.urlsplit(_clean_url(url)).path
    match = _EXT_RE.search(path)
    if match:
        ext = match.group(1).lower().replace("jpeg", "jpg")
        return "." + ext
    return ".mp4" if media_type == "video" else ".jpg"


@dataclass
class MediaItem:
    """One downloadable file: an image, or the video of a video/carousel post."""

    index: int
    media_type: str  # "image" | "video"
    url: str
    width: int = 0
    height: int = 0

    @property
    def extension(self) -> str:
        return extension_for(self.url, self.media_type)

    @property
    def label(self) -> str:
        return f"{self.media_type} {self.width}x{self.height}".strip()

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "media_type": self.media_type,
            "url": self.url,
            "width": self.width,
            "height": self.height,
            "extension": self.extension,
        }


@dataclass
class Post:
    shortcode: str
    id: str = ""
    taken_at: int = 0
    caption: str = ""
    like_count: int = 0
    comment_count: int = 0
    media_type: str = "image"  # "image" | "video" | "carousel"
    owner_username: str = ""
    media: list[MediaItem] = field(default_factory=list)
    accessibility_caption: str = ""
    thumbnail_url: str = ""  # poster frame / cover image, used by the UI grid
    width: int = 0
    height: int = 0

    # ------------------------------------------------------------------ helpers
    @property
    def url(self) -> str:
        return f"https://www.instagram.com/p/{self.shortcode}/"

    @property
    def date(self) -> _dt.datetime:
        return _dt.datetime.fromtimestamp(self.taken_at, tz=_dt.timezone.utc)

    @property
    def date_str(self) -> str:
        return self.date.strftime("%Y-%m-%d")

    @property
    def iso_date(self) -> str:
        return self.date.isoformat()

    @property
    def media_count(self) -> int:
        return len(self.media)

    @property
    def has_video(self) -> bool:
        return any(item.media_type == "video" for item in self.media)

    def iter_media(self, *, include_videos: bool = True) -> Iterable[MediaItem]:
        for item in self.media:
            if item.media_type == "video" and not include_videos:
                continue
            yield item

    def to_dict(self) -> dict:
        return {
            "shortcode": self.shortcode,
            "id": self.id,
            "url": self.url,
            "taken_at": self.taken_at,
            "date": self.iso_date if self.taken_at else None,
            "caption": self.caption,
            "like_count": self.like_count,
            "comment_count": self.comment_count,
            "media_type": self.media_type,
            "owner_username": self.owner_username,
            "media_count": self.media_count,
            "thumbnail_url": self.thumbnail_url,
            "width": self.width,
            "height": self.height,
            "media": [item.to_dict() for item in self.media],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Post":
        return cls(
            shortcode=data["shortcode"],
            id=str(data.get("id") or ""),
            taken_at=int(data.get("taken_at") or 0),
            caption=data.get("caption") or "",
            like_count=int(data.get("like_count") or 0),
            comment_count=int(data.get("comment_count") or 0),
            media_type=data.get("media_type") or "image",
            owner_username=data.get("owner_username") or "",
            media=[
                MediaItem(
                    index=int(item.get("index") or position),
                    media_type=item.get("media_type") or "image",
                    url=item.get("url") or "",
                    width=int(item.get("width") or 0),
                    height=int(item.get("height") or 0),
                )
                for position, item in enumerate(data.get("media") or [], start=1)
            ],
            thumbnail_url=data.get("thumbnail_url") or "",
            width=int(data.get("width") or 0),
            height=int(data.get("height") or 0),
        )


@dataclass
class Profile:
    id: str
    username: str
    full_name: str = ""
    biography: str = ""
    followers: int = 0
    following: int = 0
    posts_count: int = 0
    is_private: bool = False
    is_verified: bool = False
    profile_pic_url: str = ""
    external_url: str = ""
    posts: list[Post] = field(default_factory=list)
    has_next_page: bool = False
    end_cursor: str | None = None

    @property
    def url(self) -> str:
        return f"https://www.instagram.com/{self.username}/"

    def to_dict(self, *, include_posts: bool = True) -> dict:
        data: dict[str, Any] = {
            "id": self.id,
            "username": self.username,
            "full_name": self.full_name,
            "biography": self.biography,
            "followers": self.followers,
            "following": self.following,
            "posts_count": self.posts_count,
            "is_private": self.is_private,
            "is_verified": self.is_verified,
            "profile_pic_url": self.profile_pic_url,
            "external_url": self.external_url,
            "url": self.url,
        }
        if include_posts:
            data["posts"] = [post.to_dict() for post in self.posts]
            data["has_next_page"] = self.has_next_page
            data["end_cursor"] = self.end_cursor
        return data

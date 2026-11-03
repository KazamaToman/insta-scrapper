"""On-disk layout: where files go, plus the metadata index for each account.

Layout::

    downloads/
      nasa/
        profile.jpg
        metadata.json        # profile + every post we have ever seen
        posts.csv            # flat index you can open in a spreadsheet
        2024-05-01_C7xYz_01.jpg
        2024-05-01_C7xYz_02.mp4
"""

from __future__ import annotations

import csv
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .models import MediaItem, Post, Profile


def safe_name(value: str, fallback: str = "unknown") -> str:
    """Make a string safe for a file name on every OS."""
    value = (value or "").strip()
    value = re.sub(r"[^\w.\-]+", "_", value, flags=re.UNICODE)
    value = re.sub(r"_+", "_", value).strip("._")
    return value or fallback


@dataclass
class AccountPaths:
    root: Path
    username: str

    @property
    def directory(self) -> Path:
        return self.root / safe_name(self.username, "account")

    @property
    def metadata(self) -> Path:
        return self.directory / "metadata.json"

    @property
    def index(self) -> Path:
        return self.directory / "posts.csv"

    @property
    def profile_pic(self) -> Path:
        return self.directory / "profile.jpg"

    def media(self, post: Post, item: MediaItem) -> Path:
        date = post.date_str if post.taken_at else "undated"
        base = f"{date}_{safe_name(post.shortcode, 'post')}"
        suffix = f"_{item.index:02d}" if post.media_count > 1 else ""
        return self.directory / f"{base}{suffix}{item.extension}"


class Store:
    """Reads and writes the download tree."""

    def __init__(self, root: str | os.PathLike = "downloads") -> None:
        self.root = Path(root).expanduser()

    def paths(self, username: str) -> AccountPaths:
        return AccountPaths(self.root, username)

    def ensure(self, username: str) -> AccountPaths:
        paths = self.paths(username)
        paths.directory.mkdir(parents=True, exist_ok=True)
        return paths

    # ------------------------------------------------------------------ metadata
    def load_metadata(self, username: str) -> dict:
        paths = self.paths(username)
        if not paths.metadata.exists():
            return {}
        try:
            return json.loads(paths.metadata.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}

    def save_metadata(self, profile: Profile, posts: Iterable[Post]) -> Path:
        """Merge freshly fetched posts into ``metadata.json`` and refresh the CSV."""
        paths = self.ensure(profile.username)
        existing = self.load_metadata(profile.username)
        merged: dict[str, dict] = {}

        for post in existing.get("posts") or []:
            shortcode = post.get("shortcode")
            if shortcode:
                merged[shortcode] = post
        for post in posts:
            merged[post.shortcode] = post.to_dict()

        ordered = sorted(merged.values(), key=lambda item: item.get("taken_at") or 0, reverse=True)
        data = {
            "username": profile.username,
            "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "profile": {
                "id": profile.id,
                "username": profile.username,
                "full_name": profile.full_name,
                "biography": profile.biography,
                "followers": profile.followers,
                "following": profile.following,
                "posts_count": profile.posts_count,
                "is_private": profile.is_private,
                "is_verified": profile.is_verified,
                "profile_pic_url": profile.profile_pic_url,
                "external_url": profile.external_url,
                "url": profile.url,
            },
            "post_count": len(ordered),
            "posts": ordered,
        }
        paths.metadata.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        self.write_index(profile.username, ordered)
        return paths.metadata

    def write_index(self, username: str, posts: list[dict]) -> Path:
        paths = self.ensure(username)
        with open(paths.index, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["shortcode", "date", "type", "likes", "comments", "media", "url", "caption", "files"]
            )
            for post in posts:
                files = self.files_for(post)
                writer.writerow(
                    [
                        post.get("shortcode", ""),
                        (post.get("date") or "")[:10],
                        post.get("media_type", ""),
                        post.get("like_count", 0),
                        post.get("comment_count", 0),
                        post.get("media_count", 0),
                        post.get("url", ""),
                        (post.get("caption") or "").replace("\n", " ")[:300],
                        " ".join(files),
                    ]
                )
        return paths.index

    def files_for(self, post_dict: dict) -> list[str]:
        """Names of files on disk belonging to one post (from its metadata dict)."""
        username = post_dict.get("owner_username") or ""
        paths = self.paths(username)
        if not paths.directory.exists():
            return []
        prefix = f"{'%s' % ((post_dict.get('date') or 'undated')[:10])}_{safe_name(post_dict.get('shortcode', ''))}"
        return sorted(
            entry.name
            for entry in paths.directory.iterdir()
            if entry.is_file() and entry.name.startswith(prefix)
        )

    # ------------------------------------------------------------------- library
    def list_accounts(self) -> list[dict]:
        """Summarise every account folder for the web UI library view."""
        if not self.root.exists():
            return []
        accounts = []
        for folder in sorted(self.root.iterdir()):
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            media = [
                entry
                for entry in folder.iterdir()
                if entry.is_file() and entry.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".m4v"}
            ]
            meta = self.load_metadata(folder.name)
            accounts.append(
                {
                    "username": folder.name,
                    "files": len(media),
                    "bytes": sum(entry.stat().st_size for entry in media),
                    "posts": len(meta.get("posts") or []),
                    "scraped_at": meta.get("scraped_at"),
                    "full_name": (meta.get("profile") or {}).get("full_name", ""),
                }
            )
        return accounts

    def library(self, username: str) -> dict:
        """Everything we have on disk for one account, shaped for the UI."""
        paths = self.paths(username)
        meta = self.load_metadata(username)
        files = []
        if paths.directory.exists():
            for entry in sorted(paths.directory.iterdir()):
                if entry.is_file() and entry.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".m4v"}:
                    files.append(
                        {
                            "name": entry.name,
                            "bytes": entry.stat().st_size,
                            "type": "video" if entry.suffix.lower() in {".mp4", ".mov", ".m4v"} else "image",
                            "url": f"/downloads/{safe_name(username)}/{entry.name}",
                            "modified": int(entry.stat().st_mtime),
                        }
                    )
        return {
            "username": username,
            "profile": meta.get("profile") or {},
            "posts": meta.get("posts") or [],
            "files": files,
            "bytes": sum(item["bytes"] for item in files),
            "scraped_at": meta.get("scraped_at"),
        }

    def has_post(self, username: str, post: Post) -> bool:
        paths = self.paths(username)
        if not paths.directory.exists():
            return False
        return all(paths.media(post, item).exists() for item in post.media)

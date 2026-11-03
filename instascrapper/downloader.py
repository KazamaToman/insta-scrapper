"""Download posts (images, videos, carousels) and profile pictures."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .client import Instagram
from .http import download_to_file
from .models import MediaItem, Post, Profile
from .store import AccountPaths, Store

CDN_HEADERS = {"Referer": "https://www.instagram.com/", "Accept": "image/avif,image/webp,image/*,video/*,*/*;q=0.8"}

EventCallback = Callable[[dict], None]


@dataclass
class FileResult:
    shortcode: str
    filename: str
    path: str
    bytes: int = 0
    media_type: str = "image"
    status: str = "downloaded"  # downloaded | skipped | failed | planned
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "shortcode": self.shortcode,
            "filename": self.filename,
            "path": self.path,
            "bytes": self.bytes,
            "media_type": self.media_type,
            "status": self.status,
            "error": self.error,
        }


@dataclass
class Summary:
    username: str
    posts: int = 0
    files: list[FileResult] = field(default_factory=list)
    duration: float = 0.0
    error: str | None = None

    @property
    def downloaded(self) -> list[FileResult]:
        return [f for f in self.files if f.status == "downloaded"]

    @property
    def skipped(self) -> list[FileResult]:
        return [f for f in self.files if f.status == "skipped"]

    @property
    def failed(self) -> list[FileResult]:
        return [f for f in self.files if f.status == "failed"]

    @property
    def total_bytes(self) -> int:
        return sum(f.bytes for f in self.files)

    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "posts": self.posts,
            "files": [f.to_dict() for f in self.files],
            "downloaded": len(self.downloaded),
            "skipped": len(self.skipped),
            "failed": len(self.failed),
            "bytes": self.total_bytes,
            "duration": round(self.duration, 2),
            "error": self.error,
        }


class Downloader:
    """Saves profile pictures and post media into a :class:`~instascrapper.store.Store`."""

    def __init__(
        self,
        output: str | Path = "downloads",
        *,
        client: Instagram | None = None,
        workers: int = 4,
        overwrite: bool = False,
        include_videos: bool = True,
        dry_run: bool = False,
        on_event: EventCallback | None = None,
    ) -> None:
        self.store = Store(output)
        self.client = client or Instagram()
        self.workers = max(1, int(workers))
        self.overwrite = overwrite
        self.include_videos = include_videos
        self.dry_run = dry_run
        self.on_event = on_event
        self._lock = threading.Lock()
        self._bytes_written = 0

    # ------------------------------------------------------------------- events
    def _emit(self, event: dict) -> None:
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:  # pragma: no cover - a broken callback must not kill a download
                pass

    # ------------------------------------------------------------------ accounts
    def download_account(
        self,
        username: str,
        *,
        limit: int | None = None,
        profile: Profile | None = None,
        posts: Sequence[Post] | None = None,
        save_metadata: bool = True,
    ) -> Summary:
        """Fetch (unless supplied) and download an account's posts."""
        started = time.monotonic()
        profile = profile or self.client.get_profile(username)
        if profile.is_private:
            from .errors import PrivateAccountError

            raise PrivateAccountError(f"@{profile.username} is private")

        selected = list(posts) if posts is not None else list(self.client.iter_posts(profile, limit=limit))
        for post in selected:
            post.owner_username = post.owner_username or profile.username

        self._emit({"type": "account", "username": profile.username, "posts": len(selected)})
        if save_metadata:
            self.store.save_metadata(profile, selected)

        summary = Summary(username=profile.username, posts=len(selected))
        summary.files.extend(self._download_profile_picture(profile))
        summary.files.extend(self.download_posts(profile.username, selected))
        summary.duration = time.monotonic() - started

        if save_metadata:
            self.store.save_metadata(profile, selected)
        self._emit({"type": "done", **summary.to_dict()})
        return summary

    def download_posts(self, username: str, posts: Iterable[Post]) -> list[FileResult]:
        """Download every media file of the given posts (in parallel)."""
        paths = self.store.ensure(username)
        tasks: list[tuple[Post, MediaItem]] = []
        for post in posts:
            items = list(post.iter_media(include_videos=self.include_videos))
            if not items:
                self._emit({"type": "post-skipped", "shortcode": post.shortcode, "reason": "no downloadable media"})
                continue
            self._emit(
                {
                    "type": "post",
                    "shortcode": post.shortcode,
                    "media_type": post.media_type,
                    "files": len(items),
                    "caption": (post.caption or "")[:120],
                    "date": post.date_str if post.taken_at else None,
                }
            )
            tasks.extend((post, item) for item in items)

        if not tasks:
            return []

        results: list[FileResult] = []
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            for result in pool.map(lambda task: self._download_one(paths, *task), tasks):
                results.append(result)
                self._emit({"type": "file", **result.to_dict()})
        return results

    def download_single_post(self, post: Post, *, username: str | None = None) -> Summary:
        """Download one post, saving it under its owner's folder."""
        owner = username or post.owner_username or "unknown"
        started = time.monotonic()
        self._emit({"type": "account", "username": owner, "posts": 1})
        files = self.download_posts(owner, [post])
        summary = Summary(username=owner, posts=1, files=files, duration=time.monotonic() - started)
        self._emit({"type": "done", **summary.to_dict()})
        return summary

    # -------------------------------------------------------------------- workers
    def _download_profile_picture(self, profile: Profile) -> list[FileResult]:
        if not profile.profile_pic_url:
            return []
        paths = self.store.ensure(profile.username)
        destination = paths.profile_pic
        if destination.exists() and destination.stat().st_size > 0 and not self.overwrite:
            return [FileResult("", destination.name, str(destination), destination.stat().st_size, status="skipped")]
        if self.dry_run:
            return [FileResult("", destination.name, str(destination), 0, status="planned")]
        try:
            written = download_to_file(profile.profile_pic_url, str(destination), session=self.client.session,
                                       headers=CDN_HEADERS)
            self._emit({"type": "profile-picture", "filename": destination.name, "bytes": written})
            return [FileResult("", destination.name, str(destination), written, status="downloaded")]
        except Exception as exc:  # profile pic is nice-to-have, never fatal
            self._emit({"type": "warning", "message": f"profile picture failed: {exc}"})
            return [FileResult("", destination.name, str(destination), 0, status="failed", error=str(exc))]

    def _download_one(self, paths: AccountPaths, post: Post, item: MediaItem) -> FileResult:
        destination = paths.media(post, item)
        filename = destination.name

        if destination.exists() and destination.stat().st_size > 0 and not self.overwrite:
            return FileResult(post.shortcode, filename, str(destination), destination.stat().st_size,
                              item.media_type, status="skipped")

        if self.dry_run:
            return FileResult(post.shortcode, filename, str(destination), 0, item.media_type, status="planned")

        self._emit({"type": "start", "shortcode": post.shortcode, "filename": filename})
        try:
            written = download_to_file(
                item.url,
                str(destination),
                session=self.client.session,
                headers=CDN_HEADERS,
                progress=self._make_progress(post.shortcode, filename),
            )
            with self._lock:
                self._bytes_written += written
            return FileResult(post.shortcode, filename, str(destination), written, item.media_type)
        except Exception as exc:
            return FileResult(post.shortcode, filename, str(destination), 0, item.media_type,
                              status="failed", error=str(exc))

    def _make_progress(self, shortcode: str, filename: str):
        state = {"last": 0.0}

        def progress(done: int, total: int) -> None:
            now = time.monotonic()
            if now - state["last"] < 0.5 and done != total:
                return
            state["last"] = now
            self._emit(
                {
                    "type": "file-progress",
                    "shortcode": shortcode,
                    "filename": filename,
                    "done": done,
                    "total": total,
                }
            )

        return progress

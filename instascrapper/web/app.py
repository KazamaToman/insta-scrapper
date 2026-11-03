"""FastAPI application powering the web UI.

Endpoints
---------
GET  /api/health                  server + session status
GET  /api/profile/{username}      profile + first N posts
POST /api/download                start a download job
GET  /api/jobs/{job_id}           poll a job
GET  /api/jobs/{job_id}/events    stream a job over SSE
POST /api/jobs/{job_id}/cancel    cancel a job
GET  /api/library                 accounts already on disk
GET  /api/library/{username}      one account's files + metadata
GET  /downloads/...               the downloaded files themselves
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..client import Instagram, normalise_username
from ..downloader import Downloader
from ..errors import InstagramError
from ..store import Store
from .demo import demo_payload

STATIC_DIR = Path(__file__).parent / "static"
DEMO_DIR = STATIC_DIR / "demo"

MAX_SCAN = 250  # hard stop when looking for specific shortcodes


# ------------------------------------------------------------------------- models
class DownloadRequest(BaseModel):
    username: str
    shortcodes: list[str] | None = None
    limit: int | None = Field(default=20, ge=1, le=2000)
    include_videos: bool = True
    overwrite: bool = False
    demo: bool = False


# ---------------------------------------------------------------------------- job
class Job:
    def __init__(self, job_id: str, username: str) -> None:
        self.id = job_id
        self.username = username
        self.status = "queued"  # queued | running | done | error | cancelled
        self.events: list[dict] = []
        self.summary: dict | None = None
        self.error: str | None = None
        self.created = time.time()
        self.finished_at: float | None = None
        self.cancel_requested = False
        self.condition = threading.Condition()

    def emit(self, event: dict) -> None:
        with self.condition:
            self.events.append(event)
            self.condition.notify_all()

    def finish(self, status: str, summary: dict | None = None, error: str | None = None) -> None:
        with self.condition:
            self.status = status
            self.summary = summary
            self.error = error
            self.finished_at = time.time()
            self.events.append({"type": "job", "status": status, "error": error})
            self.condition.notify_all()

    @property
    def done(self) -> bool:
        return self.status in {"done", "error", "cancelled"}

    def counters(self) -> dict:
        files = downloaded = skipped = failed = 0
        total_bytes = 0
        for event in self.events:
            if event.get("type") != "file":
                continue
            files += 1
            total_bytes += int(event.get("bytes") or 0)
            status = event.get("status")
            if status == "downloaded":
                downloaded += 1
            elif status == "skipped":
                skipped += 1
            elif status == "failed":
                failed += 1
        return {
            "files": files,
            "downloaded": downloaded,
            "skipped": skipped,
            "failed": failed,
            "bytes": total_bytes,
        }

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "status": self.status,
            "error": self.error,
            "summary": self.summary,
            "counters": self.counters(),
            "created": self.created,
            "finished_at": self.finished_at,
        }


class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, username: str, worker, *args: Any, **kwargs: Any) -> Job:
        job = Job(uuid.uuid4().hex[:12], username)
        with self._lock:
            self.jobs[job.id] = job
            if len(self.jobs) > 50:  # keep memory bounded
                for old_id in sorted(self.jobs, key=lambda key: self.jobs[key].created)[:-50]:
                    self.jobs.pop(old_id, None)
        thread = threading.Thread(target=self._run, args=(job, worker, args, kwargs), daemon=True)
        thread.start()
        return job

    @staticmethod
    def _run(job: Job, worker, args: tuple, kwargs: dict) -> None:
        job.status = "running"
        budget_start = time.time()
        try:
            summary = worker(job, *args, **kwargs)
            if job.cancel_requested:
                job.finish("cancelled", summary.to_dict() if summary else None)
            else:
                job.finish("done", summary.to_dict() if summary else None)
        except InstagramError as exc:
            job.finish("error", error=str(exc))
        except Exception as exc:  # pragma: no cover - defensive
            job.finish("error", error=f"{type(exc).__name__}: {exc}")
        finally:
            job.duration = time.time() - budget_start

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)


# --------------------------------------------------------------------------- app
def create_app(
    download_dir: str = "downloads",
    cookies: str | None = None,
    delay: float = 1.0,
    proxy: str | None = None,
) -> FastAPI:
    app = FastAPI(title="instascrapper", version=__version__, docs_url="/api/docs")
    store = Store(download_dir)
    store.root.mkdir(parents=True, exist_ok=True)
    jobs = JobManager()
    client = Instagram(cookies=cookies, delay=delay, proxy=proxy)

    app.state.store = store
    app.state.client = client
    app.state.jobs = jobs

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    if DEMO_DIR.exists():
        app.mount("/demo", StaticFiles(directory=str(DEMO_DIR)), name="demo")
    app.mount("/downloads", StaticFiles(directory=str(store.root)), name="downloads")

    # ------------------------------------------------------------------ UI shell
    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    def health() -> dict:
        return {
            "ok": True,
            "version": __version__,
            "authenticated": client.authenticated,
            "download_dir": str(store.root.resolve()),
            "demo_available": DEMO_DIR.exists(),
        }

    # ------------------------------------------------------------------- profile
    @app.get("/api/profile/{username}")
    def profile(username: str, limit: int = 12, include_videos: bool = True) -> Any:
        try:
            username = normalise_username(username)
        except ValueError as exc:
            return JSONResponse({"error": str(exc), "kind": "bad_request"}, status_code=400)
        try:
            prof = client.get_profile(username)
            if prof.is_private:
                return JSONResponse(
                    {
                        "error": f"@{prof.username} is a private account. Only public accounts are supported.",
                        "kind": "private",
                    },
                    status_code=403,
                )
            posts = list(client.iter_posts(prof, limit=max(1, min(limit, 200))))
            if not include_videos:
                posts = [post for post in posts if not post.has_video]
            return {
                "demo": False,
                "authenticated": client.authenticated,
                "profile": prof.to_dict(include_posts=False),
                "posts": [post.to_dict() for post in posts],
            }
        except InstagramError as exc:
            status = 404 if exc.kind == "not_found" else 429 if exc.kind == "rate_limited" else \
                401 if exc.kind == "login_required" else 502
            return JSONResponse({"error": exc.message, "kind": exc.kind, "status": exc.status}, status_code=status)
        except ValueError as exc:
            return JSONResponse({"error": str(exc), "kind": "not_found"}, status_code=404)

    # ---------------------------------------------------------------------- demo
    @app.get("/api/demo")
    def demo() -> Any:
        if not DEMO_DIR.exists():
            return JSONResponse({"error": "demo data is not installed", "kind": "not_found"}, status_code=404)
        return demo_payload()

    # ------------------------------------------------------------------ download
    @app.post("/api/download")
    def download(payload: DownloadRequest, request: Request) -> Any:
        base_url = str(request.base_url).rstrip("/")

        def worker(job: Job, req: DownloadRequest) -> Any:
            def on_event(event: dict) -> None:
                job.emit(event)

            downloader = Downloader(
                store.root,
                client=client,
                overwrite=req.overwrite,
                include_videos=req.include_videos,
                dry_run=False,
                on_event=on_event,
            )
            if req.demo:
                prof, posts = _demo_objects(base_url)
                if req.shortcodes:
                    wanted = set(req.shortcodes)
                    posts = [post for post in posts if post.shortcode in wanted]
                return downloader.download_account(prof.username, profile=prof, posts=posts)

            prof = client.get_profile(normalise_username(req.username))
            if prof.is_private:
                from ..errors import PrivateAccountError

                raise PrivateAccountError(f"@{prof.username} is private")
            if req.shortcodes:
                wanted = set(req.shortcodes)
                selected = []
                for scanned, post in enumerate(client.iter_posts(prof), start=1):
                    if post.shortcode in wanted:
                        selected.append(post)
                        if len(selected) == len(wanted):
                            break
                    if scanned >= MAX_SCAN:
                        break
                posts = selected
            else:
                posts = list(client.iter_posts(prof, limit=req.limit))
            return downloader.download_account(prof.username, profile=prof, posts=posts)

        job = jobs.create(payload.username or "demo", worker, payload)
        return {"job_id": job.id, "status": job.status}

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str) -> Any:
        job = jobs.get(job_id)
        if not job:
            return JSONResponse({"error": "unknown job", "kind": "not_found"}, status_code=404)
        return job.snapshot()

    @app.get("/api/jobs/{job_id}/events")
    def job_events(job_id: str) -> Any:
        job = jobs.get(job_id)
        if not job:
            return JSONResponse({"error": "unknown job", "kind": "not_found"}, status_code=404)

        def stream():
            index = 0
            idle = 0.0
            yield f"data: {json.dumps(job.snapshot())}\n\n"
            while True:
                with job.condition:
                    if index >= len(job.events) and not job.done:
                        job.condition.wait(timeout=0.75)
                    events = job.events[index:]
                    index = len(job.events)
                    done = job.done
                for event in events:
                    yield f"data: {json.dumps(event)}\n\n"
                if done and not events:
                    yield f"event: end\ndata: {json.dumps(job.snapshot())}\n\n"
                    return
                if not events:
                    idle += 0.75
                    if idle >= 15:
                        idle = 0.0
                        yield ": keep-alive\n\n"

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
        )

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str) -> Any:
        job = jobs.get(job_id)
        if not job:
            return JSONResponse({"error": "unknown job", "kind": "not_found"}, status_code=404)
        job.cancel_requested = True
        job.emit({"type": "cancel-requested"})
        return {"ok": True}

    # ------------------------------------------------------------------- library
    @app.get("/api/library")
    def library() -> dict:
        return {"accounts": store.list_accounts(), "root": str(store.root.resolve())}

    @app.get("/api/library/{username}")
    def library_account(username: str) -> Any:
        data = store.library(username)
        if not data["files"] and not data["posts"]:
            return JSONResponse({"error": f"nothing downloaded for @{username}", "kind": "not_found"}, status_code=404)
        return data

    return app


def _demo_objects(base_url: str):
    """Build Profile/Post objects from the bundled sample data."""
    from ..models import MediaItem, Post, Profile

    payload = demo_payload()
    profile_data = payload["profile"]
    profile = Profile(
        id=str(profile_data["id"]),
        username=profile_data["username"],
        full_name=profile_data["full_name"],
        biography=profile_data["biography"],
        followers=profile_data["followers"],
        following=profile_data["following"],
        posts_count=profile_data["posts_count"],
        is_verified=profile_data.get("is_verified", False),
        profile_pic_url=f"{base_url}/static/demo/avatar.png",
    )
    posts = []
    for item in payload["posts"]:
        media = [
            MediaItem(
                index=index,
                media_type="image",
                url=f"{base_url}{entry['url']}",
                width=entry.get("width", 1080),
                height=entry.get("height", 1080),
            )
            for index, entry in enumerate(item["media"], start=1)
        ]
        posts.append(
            Post(
                shortcode=item["shortcode"],
                id=item["shortcode"],
                taken_at=item["taken_at"],
                caption=item["caption"],
                like_count=item["like_count"],
                comment_count=item["comment_count"],
                media_type=item["media_type"],
                owner_username=profile.username,
                media=media,
                thumbnail_url=media[0].url if media else "",
                width=1080,
                height=1080,
            )
        )
    profile.posts = posts
    return profile, posts

"""A tiny fake Instagram server used by the offline test-suite.

Serves the two JSON shapes the real client consumes (web_profile_info and the
api/v1 feed), paginates, and hands out fake media bytes so the downloader can be
exercised end to end without touching the network.
"""

from __future__ import annotations

import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# 1x1 transparent PNG
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c63000100000500010d0a2db40000"
    "000049454e44ae426082"
)


def graph_post(shortcode: str, taken_at: int = 1_700_000_000, video: bool = False, carousel: int = 0) -> dict:
    node = {
        "__typename": "GraphVideo" if video else "GraphImage",
        "id": f"id_{shortcode}",
        "shortcode": shortcode,
        "dimensions": {"width": 1080, "height": 1350},
        "display_url": f"https://cdn.example.com/{shortcode}.jpg",
        "is_video": video,
        "edge_media_to_caption": {"edges": [{"node": {"text": f"caption for {shortcode} #scraped"}}]},
        "edge_media_preview_like": {"count": 1234},
        "edge_media_to_comment": {"count": 12},
        "taken_at_timestamp": taken_at,
        "owner": {"id": "123", "username": "fakeuser"},
        "accessibility_caption": f"a photo for {shortcode}",
    }
    if video:
        node["video_url"] = f"https://cdn.example.com/{shortcode}.mp4"
    if carousel:
        node["__typename"] = "GraphSidecar"
        node["edge_sidecar_to_children"] = {
            "edges": [
                {
                    "node": {
                        "id": f"{shortcode}_{index}",
                        "shortcode": shortcode,
                        "display_url": f"https://cdn.example.com/{shortcode}_{index}.jpg",
                        "dimensions": {"width": 1080, "height": 1350},
                        "is_video": index == 2,
                        "video_url": f"https://cdn.example.com/{shortcode}_{index}.mp4" if index == 2 else None,
                    }
                }
                for index in range(1, carousel + 1)
            ]
        }
    return node


def feed_item(shortcode: str, taken_at: int = 1_700_000_000, video: bool = False) -> dict:
    return {
        "id": f"999_{shortcode}",
        "code": shortcode,
        "media_type": 2 if video else 1,
        "image_versions2": {
            "candidates": [
                {"width": 640, "height": 800, "url": f"https://cdn.example.com/{shortcode}_640.jpg"},
                {"width": 1080, "height": 1350, "url": f"https://cdn.example.com/{shortcode}.jpg"},
            ]
        },
        "video_versions": [{"width": 720, "height": 900, "url": f"https://cdn.example.com/{shortcode}.mp4"}] if video else [],
        "caption": {"text": f"feed caption {shortcode}"},
        "like_count": 42,
        "comment_count": 7,
        "taken_at": taken_at,
        "user": {"pk": 123, "username": "fakeuser"},
    }


PROFILE = {
    "data": {
        "user": {
            "id": "123",
            "username": "fakeuser",
            "full_name": "Fake User",
            "biography": "testing bio\nsecond line",
            "edge_followed_by": {"count": 2500},
            "edge_follow": {"count": 133},
            "is_private": False,
            "is_verified": True,
            "profile_pic_url_hd": "https://cdn.example.com/avatar.jpg",
            "external_url": "https://example.com",
            "edge_owner_to_timeline_media": {
                "count": 15,
                "page_info": {"has_next_page": True, "end_cursor": "CURSOR_1"},
                "edges": [
                    {"node": graph_post("AAA111", 1_700_000_100, carousel=3)},
                    {"node": graph_post("BBB222", 1_700_000_000, video=True)},
                ],
            },
        }
    },
    "status": "ok",
}

PAGE_2 = {
    "items": [feed_item("CCC333", 1_699_000_000), feed_item("DDD444", 1_698_000_000, video=True)],
    "more_available": True,
    "next_max_id": "CURSOR_2",
}

PAGE_3 = {"items": [feed_item("EEE555", 1_697_000_000)], "more_available": False, "next_max_id": None}


# Pristine copies: every test re-targets these at its own server port.
_ORIGINAL_PROFILE = copy.deepcopy(PROFILE)
_ORIGINAL_PAGE_2 = copy.deepcopy(PAGE_2)
_ORIGINAL_PAGE_3 = copy.deepcopy(PAGE_3)

_CDN = "https://cdn.example.com"


def _retarget(payload: dict, base: str) -> dict:
    """Point every fake CDN URL at the running fake server."""
    return json.loads(json.dumps(payload).replace(_CDN, base + "/media"))


def sample_profile() -> dict:
    """A pristine copy of the profile payload (never mutated by tests)."""
    return copy.deepcopy(_ORIGINAL_PROFILE)


def sample_page(n: int) -> dict:
    return copy.deepcopy(_ORIGINAL_PAGE_2 if n == 2 else _ORIGINAL_PAGE_3)


def configure(base: str) -> None:
    """Re-target the payloads at a running server (call from setUp)."""
    global PROFILE, PAGE_2, PAGE_3
    PROFILE = _retarget(_ORIGINAL_PROFILE, base)
    PAGE_2 = _retarget(_ORIGINAL_PAGE_2, base)
    PAGE_3 = _retarget(_ORIGINAL_PAGE_3, base)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence test output
        pass

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path.endswith("/users/web_profile_info/"):
            username = (query.get("username") or [""])[0]
            if username != "fakeuser":
                return self._json({"message": "User not found"}, status=404)
            return self._json(PROFILE)

        if "/feed/user/" in parsed.path:
            cursor = (query.get("max_id") or [None])[0]
            if cursor == "CURSOR_1":
                return self._json(PAGE_2)
            if cursor == "CURSOR_2":
                return self._json(PAGE_3)
            return self._json({"items": [], "more_available": False})

        if parsed.path.startswith("/media/"):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(PNG_BYTES)))
            self.end_headers()
            self.wfile.write(PNG_BYTES)
            return

        if parsed.path.startswith("/broken/"):
            return self._json({"message": "nope"}, status=500)

        return self._json({"message": "not found"}, status=404)


class FakeInstagram:
    """Context manager that runs the fake server on a random port."""

    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self) -> "FakeInstagram":
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

"""Bundled sample data so the UI can be explored without hitting Instagram.

The demo dataset is used automatically when the sandbox (or your network) cannot
reach instagram.com, and it exercises the real download pipeline - the "posts"
are served by this same server and written through the normal
:class:`~instascrapper.downloader.Downloader`.
"""

from __future__ import annotations

import datetime as _dt

DEMO_USERNAME = "demo.studio"

_RAW_POSTS = [
    {
        "shortcode": "DEMOgLass01",
        "date": "2026-10-28T09:12:00",
        "caption": "morning light studies — glass, resin and a very patient cat. #productphotography #studio",
        "likes": 1284,
        "comments": 42,
        "media": ["p1.png", "p2.png", "p3.png"],
    },
    {
        "shortcode": "DEMOtype02",
        "date": "2026-10-24T18:40:00",
        "caption": "type specimen for the autumn identity refresh. Six weights, one grid.",
        "likes": 903,
        "comments": 17,
        "media": ["p4.png"],
    },
    {
        "shortcode": "DEMOgrad03",
        "date": "2026-10-19T12:05:00",
        "caption": "gradient experiments, take 12. Somewhere between sunset and a printer error.",
        "likes": 2210,
        "comments": 88,
        "media": ["p5.png"],
    },
    {
        "shortcode": "DEMOdesk04",
        "date": "2026-10-14T08:02:00",
        "caption": "the desk, before the mess. Coffee count: 2.",
        "likes": 671,
        "comments": 9,
        "media": ["p6.png"],
    },
    {
        "shortcode": "DEMOwire05",
        "date": "2026-10-08T16:31:00",
        "caption": "wireframe → shipping in two weeks. Behind the scenes of the studio rebrand.",
        "likes": 1455,
        "comments": 61,
        "media": ["p7.png", "p8.png"],
    },
    {
        "shortcode": "DEMOgold06",
        "date": "2026-10-02T20:18:00",
        "caption": "gold hour on the rooftop. No filter, just a very orange sky.",
        "likes": 3320,
        "comments": 132,
        "media": ["p9.png"],
    },
    {
        "shortcode": "DEMOprint07",
        "date": "2026-09-27T11:47:00",
        "caption": "risograph proofs finally landed. Two colours, infinite registration errors.",
        "likes": 812,
        "comments": 24,
        "media": ["p10.png"],
    },
    {
        "shortcode": "DEMOwrap08",
        "date": "2026-09-21T15:09:00",
        "caption": "that's a wrap on the summer campaign. Thanks for having us.",
        "likes": 1930,
        "comments": 74,
        "media": ["p2.png"],
    },
]


def _timestamp(iso: str) -> int:
    return int(_dt.datetime.fromisoformat(iso).replace(tzinfo=_dt.timezone.utc).timestamp())


def demo_payload() -> dict:
    posts = []
    for raw in _RAW_POSTS:
        media = [
            {"url": f"/demo/{name}", "width": 600, "height": 600, "media_type": "image"}
            for name in raw["media"]
        ]
        posts.append(
            {
                "shortcode": raw["shortcode"],
                "id": raw["shortcode"],
                "url": f"https://www.instagram.com/p/{raw['shortcode']}/",
                "taken_at": _timestamp(raw["date"]),
                "date": raw["date"],
                "caption": raw["caption"],
                "like_count": raw["likes"],
                "comment_count": raw["comments"],
                "media_type": "carousel" if len(media) > 1 else "image",
                "media_count": len(media),
                "thumbnail_url": media[0]["url"],
                "owner_username": DEMO_USERNAME,
                "width": 600,
                "height": 600,
                "media": media,
            }
        )

    return {
        "demo": True,
        "profile": {
            "id": "0",
            "username": DEMO_USERNAME,
            "full_name": "Demo Studio",
            "biography": "Sample data bundled with instascrapper.\nReach Instagram to load a real account.",
            "followers": 12480,
            "following": 183,
            "posts_count": len(posts),
            "is_verified": False,
            "is_private": False,
            "profile_pic_url": "/static/demo/avatar.png",
            "url": f"https://www.instagram.com/{DEMO_USERNAME}/",
        },
        "posts": posts,
    }

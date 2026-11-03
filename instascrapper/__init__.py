"""instascrapper - archive public Instagram profiles and download their media.

The scraper core has **zero third-party dependencies** (stdlib only) so it works
anywhere Python 3.9+ runs.  The optional web UI needs FastAPI + uvicorn.

Typical use::

    from instascrapper import Instagram, Downloader

    ig = Instagram(cookies="cookies.txt")
    profile = ig.get_profile("nasa")
    print(profile.full_name, profile.followers)

    dl = Downloader("downloads", client=ig)
    dl.download_profile(profile, limit=12)
"""

from .client import Instagram
from .downloader import Downloader
from .errors import (
    InstagramError,
    LoginRequiredError,
    NetworkError,
    NotFoundError,
    PrivateAccountError,
    RateLimitError,
)
from .models import MediaItem, Post, Profile

__version__ = "1.0.0"

__all__ = [
    "Instagram",
    "Downloader",
    "MediaItem",
    "Post",
    "Profile",
    "InstagramError",
    "LoginRequiredError",
    "NetworkError",
    "NotFoundError",
    "PrivateAccountError",
    "RateLimitError",
    "__version__",
]

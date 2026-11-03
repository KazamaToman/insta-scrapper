"""Exception hierarchy used across the scraper."""

from __future__ import annotations


class InstagramError(Exception):
    """Base error for anything that goes wrong while talking to Instagram."""

    kind = "error"

    def __init__(self, message: str, *, status: int | None = None, url: str | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.url = url

    def to_dict(self) -> dict:
        return {"kind": self.kind, "message": self.message, "status": self.status}


class NetworkError(InstagramError):
    """DNS failure, connection reset, timeout ... (no HTTP response at all)."""

    kind = "network"


class NotFoundError(InstagramError):
    """The account or post does not exist (HTTP 404)."""

    kind = "not_found"


class PrivateAccountError(InstagramError):
    """The account exists but is private - out of scope for a public scraper."""

    kind = "private"


class LoginRequiredError(InstagramError):
    """Instagram wants an authenticated session (HTTP 401/checkpoint).

    Fix by exporting your browser cookies to a ``cookies.txt`` file and passing
    ``--cookies`` (see README).
    """

    kind = "login_required"


class RateLimitError(InstagramError):
    """HTTP 429 - back off, or use a slower ``--delay``."""

    kind = "rate_limited"


class DownloadError(InstagramError):
    """A media file could not be downloaded."""

    kind = "download"

"""Small stdlib-only HTTP helper: retries, backoff, cookies, streaming downloads."""

from __future__ import annotations

import json as _json
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Mapping

from .errors import DownloadError, NetworkError

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


@dataclass
class Response:
    status: int
    headers: Mapping[str, str]
    body: bytes
    url: str

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self):
        return _json.loads(self.body.decode("utf-8", "replace"))


@dataclass
class HttpSession:
    """Thin wrapper over urllib with sane defaults for scraping."""

    headers: dict = field(default_factory=dict)
    cookies: dict = field(default_factory=dict)
    timeout: float = 30.0
    retries: int = 3
    backoff: float = 1.5
    proxy: str | None = None
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        self.headers.setdefault("User-Agent", DEFAULT_USER_AGENT)
        self.headers.setdefault("Accept-Language", "en-US,en;q=0.9")
        self.headers.setdefault("Accept", "*/*")
        handlers = []
        if self.proxy:
            handlers.append(urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy}))
        self._opener = urllib.request.build_opener(*handlers)

    # ------------------------------------------------------------------ helpers
    def set_cookie(self, name: str, value: str) -> None:
        self.cookies[name] = value

    def cookie_header(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items() if v)

    def _request_headers(self, extra: Mapping[str, str] | None = None) -> dict:
        headers = dict(self.headers)
        if self.cookies:
            headers["Cookie"] = self.cookie_header()
        if extra:
            headers.update({k: v for k, v in extra.items() if v is not None})
        return headers

    def _open(self, url: str, data: bytes | None, headers: dict) -> Response:
        request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return Response(response.status, dict(response.headers), response.read(), response.geturl())
        except urllib.error.HTTPError as exc:  # keep the body, it carries IG's error JSON
            try:
                body = exc.read()
            except Exception:  # pragma: no cover - defensive
                body = b""
            return Response(exc.code, dict(exc.headers or {}), body, url)
        except (urllib.error.URLError, socket.timeout, ConnectionError, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            raise NetworkError(f"could not reach {urllib.parse.urlsplit(url).netloc}: {reason}") from exc

    def request(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        data: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        retries: int | None = None,
    ) -> Response:
        if params:
            separator = "&" if urllib.parse.urlsplit(url).query else "?"
            url = f"{url}{separator}{urllib.parse.urlencode(params)}"
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        attempts = self.retries if retries is None else retries
        last: Response | None = None

        for attempt in range(attempts + 1):
            response = self._open(url, body, self._request_headers(headers))
            if response.status not in RETRY_STATUS:
                return response
            last = response
            if attempt < attempts:
                wait = self.backoff * (2**attempt) + random.uniform(0, 0.5)
                retry_after = response.headers.get("Retry-After") or response.headers.get("retry-after")
                if retry_after and retry_after.strip().isdigit():
                    wait = max(wait, min(float(retry_after), 60.0))
                self.sleep(wait)
        assert last is not None
        return last

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Response:
        return self.request(url, params=params, headers=headers)


def download_to_file(
    url: str,
    destination: str,
    *,
    session: HttpSession,
    headers: Mapping[str, str] | None = None,
    progress: Callable[[int, int], None] | None = None,
    chunk_size: int = 64 * 1024,
    retries: int = 3,
) -> int:
    """Stream ``url`` to ``destination`` atomically, returning the byte count.

    Writes to ``destination + ".part"`` and renames on success so an interrupted
    run never leaves a truncated image behind.
    """
    tmp = f"{destination}.part"
    last_error: Exception | None = None

    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers=session._request_headers(headers))
            with session._opener.open(request, timeout=session.timeout) as response:
                total = int(response.headers.get("Content-Length") or 0)
                written = 0
                with open(tmp, "wb") as handle:
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        handle.write(chunk)
                        written += len(chunk)
                        if progress:
                            progress(written, total)
            if written == 0:
                raise DownloadError(f"empty response for {url}", url=url)
            import os

            os.replace(tmp, destination)
            return written
        except urllib.error.HTTPError as exc:
            last_error = DownloadError(f"HTTP {exc.code} while downloading media", status=exc.code, url=url)
            if exc.code not in RETRY_STATUS:
                break
        except (urllib.error.URLError, socket.timeout, ConnectionError, TimeoutError) as exc:
            last_error = DownloadError(f"connection error while downloading media: {exc}", url=url)
        if attempt < retries:
            session.sleep(session.backoff * (2**attempt) + random.uniform(0, 0.5))

    try:
        import os

        if os.path.exists(tmp):
            os.remove(tmp)
    except OSError:  # pragma: no cover
        pass
    raise last_error or DownloadError(f"could not download {url}", url=url)

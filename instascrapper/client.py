"""Instagram public-data client.

Only public, unauthenticated endpoints are used, plus (optionally) the session
cookies you export from a browser you are already logged into.  Instagram
throttles anonymous traffic hard, so a session cookie makes the tool far more
reliable - see the README.

Endpoints change from time to time.  They are all declared in one block at the
top of this module so a fix is a one-line edit.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Iterator, Mapping

from .errors import (
    InstagramError,
    LoginRequiredError,
    NotFoundError,
    PrivateAccountError,
    RateLimitError,
)
from .http import DEFAULT_USER_AGENT, HttpSession, Response
from .models import Post, Profile, _clean_url
from .parsers import parse_feed_item, parse_graph_media, parse_profile

# --------------------------------------------------------------------- endpoints
BASE = "https://www.instagram.com"
API = "https://i.instagram.com/api/v1"

WEB_PROFILE_INFO = f"{API}/users/web_profile_info/"
FEED_USER = f"{API}/feed/user/{{pk}}/"
GRAPHQL_QUERY = f"{BASE}/graphql/query/"
POST_JSON = f"{BASE}/p/{{shortcode}}/?__a=1&__d=dis"
POST_EMBED = f"{BASE}/p/{{shortcode}}/embed/captioned/"
PROFILE_HTML = f"{BASE}/{{username}}/"

WEB_APP_ID = "936619743392459"
TIMELINE_QUERY_HASH = os.environ.get("IG_QUERY_HASH", "e769aa130647d2354c40ea6a439bfc08")

USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")


def normalise_username(value: str) -> str:
    """Accept ``@nasa``, ``nasa`` or a full profile URL and return ``nasa``."""
    value = (value or "").strip()
    if not value:
        raise ValueError("username is empty")
    if "instagram.com" in value:
        match = re.search(r"instagram\.com/([^/?#]+)", value)
        value = match.group(1) if match else value
        if value in {"p", "reel", "tv", "stories"}:
            raise ValueError("that is a post URL, not a profile URL")
    value = value.lstrip("@").strip("/")
    value = value.split("?")[0]
    if not USERNAME_RE.match(value):
        raise ValueError(f"'{value}' is not a valid Instagram username")
    return value


def shortcode_from_url(value: str) -> str:
    """Extract the shortcode from a post/reel URL or return a bare shortcode."""
    value = (value or "").strip()
    match = re.search(r"instagram\.com/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)", value)
    if match:
        return match.group(1)
    value = value.strip("/").split("?")[0]
    if re.fullmatch(r"[A-Za-z0-9_-]{5,20}", value):
        return value
    raise ValueError(f"could not find a post shortcode in '{value}'")


# ------------------------------------------------------------------ cookie loading
def load_cookies(source: str | Mapping[str, str] | None) -> dict[str, str]:
    """Read cookies from a Netscape ``cookies.txt`` file, a JSON file or a dict.

    Works with files exported by the "Get cookies.txt" browser extensions as
    well as instaloader's session files.
    """
    if source is None:
        cookies: dict[str, str] = {}
        for name in ("IG_SESSIONID", "IG_CSRFTOKEN", "IG_DS_USER_ID", "IG_MID"):
            value = os.environ.get(name)
            if value:
                cookies[name[3:].lower()] = value.strip()
        return cookies

    if isinstance(source, Mapping):
        return {str(k): str(v) for k, v in source.items() if v}

    path = os.path.expanduser(str(source))
    if not os.path.exists(path):
        raise FileNotFoundError(f"cookie file not found: {path}")

    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        text = handle.read()

    stripped = text.lstrip()
    if stripped.startswith("{"):
        data = json.loads(stripped)
        if isinstance(data, dict) and "cookies" in data and isinstance(data["cookies"], list):
            return {item["name"]: item["value"] for item in data["cookies"] if item.get("name")}
        return {str(k): str(v) for k, v in data.items() if isinstance(v, (str, int))}

    cookies = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") and not line.startswith("#HttpOnly_"):
            continue
        line = line.replace("#HttpOnly_", "", 1)
        parts = line.split("\t")
        if len(parts) >= 7:
            name, value = parts[5].strip(), parts[6].strip()
            if name:
                cookies[name] = value
    return cookies


def _wants_login(response: Response) -> bool:
    body = response.text.lower()
    return any(
        marker in body
        for marker in ("login_required", "checkpoint_required", "please wait a few minutes", "challenge_required")
    )


def _find_key(obj: Any, key: str, depth: int = 0) -> Any:
    """Recursively search parsed JSON for the first value stored under ``key``."""
    if depth > 12:
        return None
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = _find_key(value, key, depth + 1)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_key(value, key, depth + 1)
            if found is not None:
                return found
    return None


class Instagram:
    """Client for public Instagram profiles and posts."""

    def __init__(
        self,
        cookies: str | Mapping[str, str] | None = None,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        delay: float = 1.0,
        timeout: float = 30.0,
        retries: int = 3,
        proxy: str | None = None,
        session: HttpSession | None = None,
    ) -> None:
        cookie_jar = load_cookies(cookies)
        self.session = session or HttpSession(
            headers={"User-Agent": user_agent, "x-ig-app-id": WEB_APP_ID},
            cookies=cookie_jar,
            timeout=timeout,
            retries=retries,
            proxy=proxy,
        )
        if cookies is not None and not self.session.cookies:
            self.session.cookies = cookie_jar
        self.delay = max(0.0, delay)
        self._lock = threading.Lock()
        self._last_request = 0.0
        self.request_count = 0

    # ------------------------------------------------------------------ internals
    @property
    def authenticated(self) -> bool:
        return "sessionid" in self.session.cookies

    def _throttle(self) -> None:
        """Space out requests so we look less like a scraper."""
        if self.delay <= 0:
            return
        with self._lock:
            elapsed = time.monotonic() - self._last_request
            wait = self.delay - elapsed
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def _get(self, url: str, *, params: Mapping[str, str] | None = None, headers: Mapping[str, str] | None = None,
             data: Mapping[str, str] | None = None) -> Response:
        self._throttle()
        self.request_count += 1
        return self.session.request(url, params=params, headers=headers, data=data)

    @staticmethod
    def _raise_for_status(response: Response, what: str) -> None:
        status = response.status
        if status == 200:
            return
        if status == 404:
            raise NotFoundError(f"{what} not found", status=status, url=response.url)
        if status == 429:
            raise RateLimitError(
                f"Instagram is rate limiting this IP while fetching {what}. "
                "Wait a few minutes, raise --delay, or use --cookies.",
                status=status,
                url=response.url,
            )
        if status in (401, 403) or _wants_login(response):
            raise LoginRequiredError(
                f"Instagram refused the request for {what} (HTTP {status}). "
                "Export your browser cookies to cookies.txt and pass --cookies cookies.txt.",
                status=status,
                url=response.url,
            )
        raise InstagramError(f"unexpected HTTP {status} while fetching {what}", status=status, url=response.url)

    # -------------------------------------------------------------------- profile
    def get_profile(self, username: str) -> Profile:
        """Fetch a public profile plus its first page of posts (~12)."""
        username = normalise_username(username)
        headers = {
            "Referer": PROFILE_HTML.format(username=username),
            "X-Requested-With": "XMLHttpRequest",
        }
        try:
            response = self._get(WEB_PROFILE_INFO, params={"username": username}, headers=headers)
            self._raise_for_status(response, f"@{username}")
            payload = response.json()
            profile = parse_profile(payload)
            if profile.is_private:
                profile.posts = []
            return profile
        except (NotFoundError, PrivateAccountError, RateLimitError):
            raise
        except (InstagramError, ValueError):
            # API blocked or payload changed shape - fall back to the public HTML page.
            return self._profile_from_html_fallback(username)

    def _profile_from_html_fallback(self, username: str) -> Profile:
        self._throttle()
        self.request_count += 1
        response = self.session.request(PROFILE_HTML.format(username=username), headers={"Accept": "text/html"})
        if response.status == 404:
            raise NotFoundError(f"@{username} does not exist", status=404)
        self._raise_for_status(response, f"@{username}")
        html = response.text

        media = None
        for blob in re.findall(r'<script type="application/json"[^>]*>(.*?)</script>', html, re.DOTALL):
            try:
                data = json.loads(blob)
            except ValueError:
                continue
            media = _find_key(data, "edge_owner_to_timeline_media")
            if media:
                user = _find_key(data, "user") or {}
                if isinstance(user, dict) and user.get("username"):
                    profile = parse_profile({"data": {"user": {**user, "edge_owner_to_timeline_media": media}}})
                    return profile
                break
        if not media:
            if "is_private" in html and re.search(r"This account is private", html):
                raise PrivateAccountError(f"@{username} is private", status=403)
            raise NotFoundError(f"@{username} does not exist", status=404)

        og_image = re.search(r'<meta property="og:image" content="([^"]+)"', html)
        og_desc = re.search(r'<meta property="og:description" content="([^"]+)"', html)
        description = og_desc.group(1) if og_desc else ""
        followers = following = posts_count = 0
        count_match = re.search(r"([\d,.]+[KM]?)\s+Followers", description)
        if count_match:
            followers = _human_to_int(count_match.group(1))
        following_match = re.search(r"([\d,.]+[KM]?)\s+Following", description)
        if following_match:
            following = _human_to_int(following_match.group(1))
        posts_match = re.search(r"([\d,.]+[KM]?)\s+Posts", description)
        if posts_match:
            posts_count = _human_to_int(posts_match.group(1))

        return Profile(
            id="",
            username=username,
            full_name="",
            biography="",
            followers=followers,
            following=following,
            posts_count=posts_count,
            profile_pic_url=_clean_url(og_image.group(1) if og_image else ""),
            posts=[],
        )

    # ---------------------------------------------------------------------- posts
    def get_posts(self, username_or_profile: str | Profile, limit: int | None = None) -> list[Post]:
        """Convenience wrapper that collects posts into a list."""
        profile = (
            username_or_profile
            if isinstance(username_or_profile, Profile)
            else self.get_profile(username_or_profile)
        )
        return list(self.iter_posts(profile, limit=limit))

    def iter_posts(self, profile: Profile, limit: int | None = None) -> Iterator[Post]:
        """Yield posts newest-first, following pagination until ``limit``.

        The first page arrives with the profile itself, so a small ``limit``
        costs exactly one HTTP request.
        """
        if profile.is_private:
            raise PrivateAccountError(
                f"@{profile.username} is private - only public accounts can be scraped"
            )

        seen: set[str] = set()
        produced = 0

        def emit(posts: list[Post]) -> Iterator[Post]:
            nonlocal produced
            for post in posts:
                if not post.shortcode or post.shortcode in seen:
                    continue
                seen.add(post.shortcode)
                if profile.username and not post.owner_username:
                    post.owner_username = profile.username
                produced += 1
                yield post
                if limit is not None and produced >= limit:
                    return

        for post in emit(profile.posts):
            yield post
        if limit is not None and produced >= limit:
            return
        if not profile.has_next_page or not profile.id:
            return

        cursor = profile.end_cursor
        while cursor:
            posts, next_cursor, more = self._next_page(profile.id, cursor)
            before = produced
            for post in emit(posts):
                yield post
            if limit is not None and produced >= limit:
                return
            if before == produced:
                break  # pragma: no cover - defensive against a stuck cursor
            if not more:
                break
            cursor = next_cursor

    def _next_page(self, pk: str, cursor: str) -> tuple[list[Post], str | None, bool]:
        try:
            response = self._get(
                FEED_USER.format(pk=pk),
                params={"count": "33", "max_id": cursor},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
            self._raise_for_status(response, "next page of posts")
            payload = response.json()
            items = payload.get("items") or []
            posts = [parse_feed_item(item) for item in items]
            return posts, payload.get("next_max_id"), bool(payload.get("more_available"))
        except (LoginRequiredError, InstagramError) as exc:
            if isinstance(exc, RateLimitError):
                raise
            posts, next_cursor, more = self._graphql_page(pk, cursor)
            return posts, next_cursor, more

    def _graphql_page(self, pk: str, cursor: str) -> tuple[list[Post], str | None, bool]:
        """Fallback pagination through the public GraphQL endpoint."""
        variables = json.dumps({"id": str(pk), "first": 33, "after": cursor}, separators=(",", ":"))
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": f"{BASE}/",
        }
        csrf = self.session.cookies.get("csrftoken")
        if csrf:
            headers["X-CSRFToken"] = csrf
        response = self._get(GRAPHQL_QUERY, data={"query_hash": TIMELINE_QUERY_HASH, "variables": variables},
                             headers=headers)
        if response.status != 200:
            raise LoginRequiredError(
                "Instagram is blocking pagination without a session. "
                "Export cookies to cookies.txt and pass --cookies cookies.txt.",
                status=response.status,
            )
        payload = response.json()
        media = _find_key(payload, "edge_owner_to_timeline_media") or {}
        posts = [parse_graph_media(edge["node"]) for edge in media.get("edges") or [] if edge.get("node")]
        page_info = media.get("page_info") or {}
        return posts, page_info.get("end_cursor"), bool(page_info.get("has_next_page"))

    # ----------------------------------------------------------------------- post
    def get_post(self, url_or_shortcode: str) -> Post:
        """Fetch a single post by URL or shortcode (public posts only)."""
        shortcode = shortcode_from_url(url_or_shortcode)
        response = self._get(
            POST_JSON.format(shortcode=shortcode),
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": POST_EMBED.format(shortcode=shortcode)},
        )
        if response.status == 200:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            post = _post_from_payload(payload)
            if post:
                return post
        return self._post_from_embed(shortcode, response)

    def _post_from_embed(self, shortcode: str, previous: Response | None = None) -> Post:
        """Public embed pages keep working without a session (single image only)."""
        response = self._get(POST_EMBED.format(shortcode=shortcode), headers={"Accept": "text/html"})
        if response.status == 404:
            raise NotFoundError(f"post {shortcode} does not exist", status=404)
        self._raise_for_status(response, f"post {shortcode}")
        html = response.text

        images = re.findall(r'class="[^"]*EmbeddedMediaImage[^"]*"[^>]*src="([^"]+)"', html)
        if not images:
            images = re.findall(r'<img[^>]+src="(https:[^"]+)"', html)
        videos = re.findall(r'<video[^>]+src="([^"]+)"', html)
        if not images and not videos:
            if previous is not None and previous.status in (401, 403):
                raise LoginRequiredError(
                    f"Instagram would not serve post {shortcode} anonymously. Try --cookies cookies.txt.",
                    status=previous.status,
                )
            raise InstagramError(f"could not read post {shortcode} from Instagram's embed page")

        caption_match = re.search(r'<div class="[^"]*Caption[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
        caption = ""
        if caption_match:
            caption = re.sub(r"<[^>]+>", " ", caption_match.group(1))
            caption = _unescape(caption).strip()

        username_match = re.search(r'class="[^"]*UsernameText[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
        username = _unescape(re.sub(r"<[^>]+>", "", username_match.group(1))).strip() if username_match else ""

        from .models import MediaItem

        media = [
            MediaItem(index=i, media_type="image", url=_clean_url(url.replace("&amp;", "&")))
            for i, url in enumerate(dict.fromkeys(images), start=1)
        ]
        for video_url in videos:
            media.append(MediaItem(index=len(media) + 1, media_type="video", url=_clean_url(video_url.replace("&amp;", "&"))))

        media_type = "carousel" if len(media) > 1 else (media[0].media_type if media else "image")
        return Post(
            shortcode=shortcode,
            caption=caption,
            media_type=media_type,
            owner_username=username,
            media=media,
        )

    # ----------------------------------------------------------------- diagnostics
    def whoami(self) -> dict:
        """Report whether a session cookie is present (no network call)."""
        return {
            "authenticated": self.authenticated,
            "user_id": self.session.cookies.get("ds_user_id") or None,
            "requests_made": self.request_count,
            "delay": self.delay,
        }


# --------------------------------------------------------------------------- helpers
def _human_to_int(value: str) -> int:
    value = value.strip().replace(",", "")
    multiplier = 1
    if value.endswith(("K", "k")):
        multiplier, value = 1_000, value[:-1]
    elif value.endswith(("M", "m")):
        multiplier, value = 1_000_000, value[:-1]
    try:
        return int(float(value) * multiplier)
    except ValueError:
        return 0


def _unescape(text: str) -> str:
    import html as html_module

    return html_module.unescape(text)


def _post_from_payload(payload: Any) -> Post | None:
    """Handle the handful of shapes ``?__a=1`` has used over the years."""
    if not isinstance(payload, dict):
        return None
    if payload.get("items"):
        try:
            return parse_feed_item(payload["items"][0])
        except (KeyError, IndexError, TypeError):
            return None
    media = _find_key(payload, "shortcode_media")
    if isinstance(media, dict):
        return parse_graph_media(media)
    if payload.get("graphql"):
        node = _find_key(payload["graphql"], "shortcode_media")
        if isinstance(node, dict):
            return parse_graph_media(node)
    return None

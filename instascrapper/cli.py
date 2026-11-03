"""Command line interface.

    python -m instascrapper profile nasa
    python -m instascrapper posts nasa --limit 30
    python -m instascrapper download nasa --limit 50 --cookies cookies.txt
    python -m instascrapper download https://www.instagram.com/p/C7xYzAbCdEf/
    python -m instascrapper serve --port 8000
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from . import __version__
from .client import Instagram, normalise_username, shortcode_from_url
from .downloader import Downloader
from .errors import InstagramError, PrivateAccountError


# ---------------------------------------------------------------------- formatting
def human_bytes(count: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if count < 1024 or unit == "TB":
            return f"{count:.0f} {unit}" if unit == "B" else f"{count:.1f} {unit}"
        count /= 1024
    return f"{count:.1f} TB"


def human_int(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def print_profile(profile) -> None:
    print(f"@{profile.username}  {profile.full_name}".rstrip())
    print(f"  {human_int(profile.followers)} followers · {human_int(profile.following)} following · "
          f"{human_int(profile.posts_count)} posts")
    flags = []
    if profile.is_verified:
        flags.append("verified")
    if profile.is_private:
        flags.append("private")
    if flags:
        print(f"  {' · '.join(flags)}")
    if profile.biography:
        for line in profile.biography.splitlines():
            print(f"  | {line}")
    if profile.external_url:
        print(f"  {profile.external_url}")
    print(f"  {profile.url}")


def print_posts(posts) -> None:
    print(f"{'date':<12} {'shortcode':<14} {'type':<9} {'files':>5} {'likes':>7}  caption")
    for post in posts:
        caption = (post.caption or "").replace("\n", " ")[:60]
        date = post.date_str if post.taken_at else "—"
        print(f"{date:<12} {post.shortcode:<14} {post.media_type:<9} {post.media_count:>5} "
              f"{human_int(post.like_count):>7}  {caption}")


# --------------------------------------------------------------------------- client
def build_client(args) -> Instagram:
    return Instagram(
        cookies=args.cookies,
        delay=args.delay,
        timeout=args.timeout,
        retries=args.retries,
        proxy=args.proxy,
    )


def progress_printer(args):
    """Stream download events to stderr as compact lines."""

    def on_event(event: dict) -> None:
        kind = event.get("type")
        if args.quiet:
            return
        if kind == "account":
            print(f"→ @{event['username']}: {event['posts']} post(s)", file=sys.stderr)
        elif kind == "start":
            print(f"  ↓ {event['filename']}", file=sys.stderr)
        elif kind == "file":
            status = event["status"]
            if status == "downloaded":
                print(f"  ✓ {event['filename']} ({human_bytes(event['bytes'])})", file=sys.stderr)
            elif status == "skipped":
                print(f"  = {event['filename']} (already downloaded)", file=sys.stderr)
            elif status == "failed":
                print(f"  ✗ {event['filename']}: {event['error']}", file=sys.stderr)
        elif kind == "warning":
            print(f"  ! {event['message']}", file=sys.stderr)

    return on_event


# ------------------------------------------------------------------------- commands
def cmd_profile(args) -> int:
    client = build_client(args)
    profile = client.get_profile(args.username)
    if args.json:
        print(json.dumps(profile.to_dict(include_posts=False), indent=2))
    else:
        print_profile(profile)
        if profile.posts and not profile.is_private:
            print()
            print_posts(profile.posts[:12])
    return 0


def cmd_posts(args) -> int:
    client = build_client(args)
    profile = client.get_profile(args.username)
    limit = None if args.all else args.limit
    posts = list(client.iter_posts(profile, limit=limit))
    if args.json:
        print(json.dumps([post.to_dict() for post in posts], indent=2))
    else:
        print_profile(profile)
        print()
        print_posts(posts)
        print(f"\n{len(posts)} post(s)")
    return 0


def cmd_download(args) -> int:
    client = build_client(args)
    target = args.target
    is_post = "/p/" in target or "/reel/" in target or "/tv/" in target

    downloader = Downloader(
        args.dir,
        client=client,
        workers=args.workers,
        overwrite=args.overwrite,
        include_videos=not args.no_videos,
        dry_run=args.dry_run,
        on_event=progress_printer(args),
    )

    if is_post:
        post = client.get_post(shortcode_from_url(target))
        summary = downloader.download_single_post(post, username=post.owner_username or None)
    else:
        profile = client.get_profile(normalise_username(target))
        if profile.is_private:
            raise PrivateAccountError(f"@{profile.username} is private")
        limit = None if args.all else args.limit
        summary = downloader.download_account(profile.username, profile=profile, limit=limit)

    if args.json:
        print(json.dumps(summary.to_dict(), indent=2))
    else:
        print(
            f"\n{len(summary.downloaded)} downloaded · {len(summary.skipped)} skipped · "
            f"{len(summary.failed)} failed · {human_bytes(summary.total_bytes)} → {args.dir}/"
        )
    return 0 if not summary.failed else 1


def cmd_serve(args) -> int:
    try:
        import uvicorn
    except ImportError:
        print("The web UI needs extra packages. Install them with:\n"
              "    pip install -r requirements.txt", file=sys.stderr)
        return 2
    from .web.app import create_app

    app = create_app(
        download_dir=args.dir,
        cookies=args.cookies,
        delay=args.delay,
        proxy=args.proxy,
    )
    print(f"instascrapper web UI → http://localhost:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


# ---------------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="instascrapper",
        description="Scrape public Instagram profiles and download their posts.",
    )
    parser.add_argument("--version", action="version", version=f"instascrapper {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--cookies", metavar="FILE", help="Netscape cookies.txt exported from your browser (recommended)")
    common.add_argument("--delay", type=float, default=1.0, help="seconds between API requests (default: 1.0)")
    common.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds (default: 30)")
    common.add_argument("--retries", type=int, default=3, help="retries per request (default: 3)")
    common.add_argument("--proxy", metavar="URL", help="HTTP(S) proxy, e.g. http://127.0.0.1:8080")
    common.add_argument("--json", action="store_true", help="machine-readable output")
    common.add_argument("-q", "--quiet", action="store_true", help="suppress progress output")

    sub = parser.add_subparsers(dest="command", required=True)

    p_profile = sub.add_parser("profile", parents=[common], help="show a public profile")
    p_profile.add_argument("username")
    p_profile.set_defaults(func=cmd_profile)

    p_posts = sub.add_parser("posts", parents=[common], help="list an account's posts")
    p_posts.add_argument("username")
    p_posts.add_argument("--limit", type=int, default=12, help="how many posts to list (default: 12)")
    p_posts.add_argument("--all", action="store_true", help="list every post")
    p_posts.set_defaults(func=cmd_posts)

    p_dl = sub.add_parser("download", parents=[common], help="download an account or a single post")
    p_dl.add_argument("target", help="username, profile URL, post URL or shortcode")
    p_dl.add_argument("--limit", type=int, default=20, help="max posts to download (default: 20)")
    p_dl.add_argument("--all", action="store_true", help="download every post on the account")
    p_dl.add_argument("--no-videos", action="store_true", help="skip video files")
    p_dl.add_argument("--dir", default="downloads", help="output directory (default: downloads)")
    p_dl.add_argument("--workers", type=int, default=4, help="parallel downloads (default: 4)")
    p_dl.add_argument("--overwrite", action="store_true", help="re-download files that already exist")
    p_dl.add_argument("--dry-run", action="store_true", help="show what would be downloaded")
    p_dl.set_defaults(func=cmd_download)

    p_serve = sub.add_parser("serve", parents=[common], help="run the web UI")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--dir", default="downloads", help="download directory (default: downloads)")
    p_serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except PrivateAccountError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except InstagramError as exc:
        print(f"error: {exc}", file=sys.stderr)
        if exc.kind == "network":
            print("hint: check your connection, or use --proxy if Instagram is blocked on your network.",
                  file=sys.stderr)
        elif exc.kind == "login_required":
            print("hint: export cookies.txt from a logged-in browser session and pass --cookies cookies.txt.",
                  file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

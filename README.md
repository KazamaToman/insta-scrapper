# instascrapper

Scrape **public** Instagram profiles and download their posts — images, videos, carousels, captions, likes and dates — from the command line or a small web UI.

The scraper core is **pure Python standard library**: no `pip install`, no browser automation, no API keys. The optional web UI adds FastAPI + uvicorn.

```bash
python -m instascrapper download nasa --limit 12
```

```
→ @nasa: 12 post(s)
  ↓ 2024-06-11_C8kLmNoPq_01.jpg
  ✓ 2024-06-11_C8kLmNoPq_01.jpg (412.6 KB)
  ✓ 2024-06-11_C8kLmNoPq_02.mp4 (4.1 MB)
  = 2024-06-09_C8hIjKlMn.jpg (already downloaded)

9 downloaded · 3 skipped · 0 failed · 38.4 MB → downloads/
```

---

## Features

| | |
|---|---|
| **Profiles** | Full name, bio, followers / following / post count, verified flag, HD profile picture |
| **Posts** | Images, videos, carousels — highest-resolution source, one file per slide (`_01`, `_02`, …) |
| **Metadata** | `metadata.json` (everything ever seen for the account) + `posts.csv` you can open in a spreadsheet |
| **Single posts** | Paste any `instagram.com/p/…` or `/reel/…` URL and download just that one |
| **Resumable** | Re-runs skip files you already have; `.part` files keep partial downloads out of your library |
| **Polite** | Per-request delay, exponential backoff on 429/5xx, bounded parallelism |
| **Session support** | Drop in a `cookies.txt` to scrape far more reliably than anonymous requests |
| **Web UI** | Search, browse a grid, select posts, live download progress over SSE, local library viewer |
| **Zero-dep core** | `python -m instascrapper …` works with a bare Python 3.9+ install |

---

## Install

```bash
git clone https://github.com/KazamaToman/insta-scrapper.git
cd insta-scrapper

# CLI only — nothing to install, just run it
python -m instascrapper profile nasa

# Web UI
pip install -r requirements.txt
python -m instascrapper serve
# → http://127.0.0.1:8000
```

Or install as a package (`instascrapper` becomes a command on your `PATH`):

```bash
pip install -e ".[web]"
instascrapper serve
```

---

## CLI

```
instascrapper profile  <username>     # who is this account?
instascrapper posts    <username>     # list posts (date, shortcode, type, likes, caption)
instascrapper download <target>       # download a profile or a single post
instascrapper serve    [--port 8000]  # web UI
```

Examples:

```bash
# Inspect a profile
python -m instascrapper profile nasa

# List the 30 newest posts, machine-readable
python -m instascrapper posts nasa --limit 30 --json

# Download the 50 newest posts, politely
python -m instascrapper download nasa --limit 50 --delay 2 --workers 3

# Everything, with an authenticated session (much more reliable)
python -m instascrapper download nasa --all --cookies cookies.txt

# One post from its URL
python -m instascrapper download https://www.instagram.com/p/C8kLmNoPq/

# See what would happen, write nothing
python -m instascrapper download nasa --limit 20 --dry-run

# Images only, custom output folder
python -m instascrapper download nasa --limit 100 --no-videos --dir ~/archive
```

| Flag | Default | Meaning |
|---|---|---|
| `--limit N` | `20` | Max posts to download |
| `--all` | off | No post limit |
| `--no-videos` | off | Skip `.mp4` files |
| `--dir` | `downloads` | Output directory |
| `--workers` | `4` | Parallel file downloads |
| `--overwrite` | off | Re-download files that already exist |
| `--dry-run` | off | Plan only, write nothing |
| `--cookies FILE` | none | Netscape `cookies.txt` (see below) |
| `--delay SEC` | `1.0` | Minimum gap between API requests |
| `--timeout SEC` | `30` | Per-request timeout |
| `--retries N` | `3` | Retries on 429/5xx/network errors |
| `--proxy URL` | none | e.g. `http://127.0.0.1:8080` |
| `--json` | off | Machine-readable output |
| `-q, --quiet` | off | No progress lines |

Exit codes: `0` success, `1` error or some files failed, `2` bad usage.

---

## Web UI

```bash
python -m instascrapper serve --port 8000
```

- Search a username, `@handle` or full profile URL → profile card + post grid.
- Click a tile to preview the caption; the circle in the corner toggles selection.
- Select posts (or “Download all shown”) → a job starts and streams progress file-by-file.
- **Library** tab browses everything on disk, with links to `metadata.json` and `posts.csv`.

Optional: run it with a session for fewer blocks —

```bash
python -m instascrapper serve --cookies cookies.txt --delay 2
```

> The UI ships with a **demo dataset** (8 sample posts served by the app itself) so you can try the download flow even on a machine where instagram.com is unreachable — e.g. a locked-down sandbox. Real scraping needs real network access to Instagram.

---

## Sessions & cookies (recommended)

Anonymous scraping works, but Instagram rate-limits it aggressively: expect `429` or “login required” after a few pages, especially from datacenter IPs.

1. Log in to Instagram in your browser.
2. Export cookies for `instagram.com` — e.g. the “Get cookies.txt LOCALLY” extension, or copy the `sessionid`, `csrftoken` and `ds_user_id` cookies from DevTools → Application → Cookies.
3. Save them as `cookies.txt` (see [`examples/cookies.txt.example`](examples/cookies.txt.example)) and pass `--cookies cookies.txt`.

Alternatives: the env vars `IG_SESSIONID`, `IG_CSRFTOKEN`, `IG_DS_USER_ID`, `IG_MID`, or a JSON file `{"sessionid": "...", "csrftoken": "..."}`.

Use a throwaway account, keep the delay at 1–3 s, and don't hammer the API. `cookies.txt` is git-ignored — never commit it.

---

## Output layout

```
downloads/
└── nasa/
    ├── profile.jpg                         # HD profile picture
    ├── metadata.json                       # profile + every post ever scraped (merged)
    ├── posts.csv                           # shortcode,date,type,likes,comments,url,caption,files
    ├── 2024-06-11_C8kLmNoPq_01.jpg         # <date>_<shortcode>_<slide>.<ext>
    ├── 2024-06-11_C8kLmNoPq_02.mp4
    └── 2024-06-09_C8hIjKlMn.jpg            # single-image post: no slide suffix
```

`metadata.json` looks like:

```json
{
  "username": "nasa",
  "scraped_at": "2026-11-03T05:44:04Z",
  "profile": { "full_name": "NASA", "followers": 97000000, "biography": "…" },
  "post_count": 1,
  "posts": [
    {
      "shortcode": "C8kLmNoPq",
      "url": "https://www.instagram.com/p/C8kLmNoPq/",
      "date": "2024-06-11T18:04:00+00:00",
      "caption": "…",
      "like_count": 512340,
      "comment_count": 2103,
      "media_type": "carousel",
      "media": [
        { "index": 1, "media_type": "image", "url": "https://…", "width": 1440, "height": 1800, "extension": ".jpg" }
      ]
    }
  ]
}
```

---

## How it works

Three public endpoints, all declared at the top of [`instascrapper/client.py`](instascrapper/client.py) so they are trivial to update when Instagram changes:

| Purpose | Request |
|---|---|
| Profile + first ~12 posts | `GET i.instagram.com/api/v1/users/web_profile_info/?username=…` with `x-ig-app-id` |
| Pagination | `GET i.instagram.com/api/v1/feed/user/<pk>/?count=33&max_id=<cursor>` |
| Single post | `GET www.instagram.com/p/<shortcode>/?__a=1&__d=dis` |

Fallbacks are built in: if the profile API is blocked, the profile HTML page is parsed (`og:` tags + embedded JSON); if the feed API refuses, pagination goes through the public GraphQL endpoint; single posts fall back to the public `/embed/captioned/` page.

Both JSON shapes Instagram returns are normalised by [`parsers.py`](instascrapper/parsers.py) into one `Post` model, so the downloader, CLI and UI never care which endpoint answered.

Media is streamed to `<file>.part` and renamed on success, so an interrupted run never leaves a half-written JPEG. Transient failures (429 / 5xx / timeouts) are retried with exponential backoff and jitter.

---

## What it does **not** do

- Private accounts — nothing authenticated-only is fetched, by design.
- Stories, highlights, DMs, follower/following lists, comments, or “who liked this”.
- Bypassing rate limits, logins, or checkpoints.

If Instagram returns `login_required` even with cookies, your session is stale or the IP is blocked — log in again in the browser and re-export.

---

## Legal & ethical use

This tool only reads data that Instagram serves publicly, but that does not automatically make every use lawful or welcome. You are responsible for what you scrape:

- Respect Instagram's Terms of Use, `robots.txt`, and the rights of the people who posted the content.
- Only archive accounts you have permission to archive, or where a legal exception applies (research, journalism, personal backup of your own account).
- Never use it for harassment, doxxing, surveillance, or republishing someone's photos as your own.
- Keep the delay high and the volume low; a scraper that hammers the API gets IPs blocked and makes life harder for everyone.

---

## Project layout

```
instascrapper/
  client.py      endpoints, pagination, cookie handling, error mapping
  parsers.py     Instagram JSON → Post / Profile (both payload shapes)
  http.py        stdlib HTTP session: retries, backoff, atomic streaming downloads
  downloader.py  parallel downloads, skip/overwrite logic, event callbacks
  store.py       folder layout, metadata.json, posts.csv, library listing
  cli.py         argparse CLI
  web/app.py     FastAPI app: profile, download jobs, SSE progress, library
  web/static/    index.html, style.css, app.js (no framework, no build step)
tests/           offline suite: fake Instagram server, parsers, downloader, API
tools/           demo asset generator
```

## Tests

```bash
python -m unittest discover -s tests -t .
```

44 tests, fully offline (they run against a local fake Instagram server) — parsers for both payload shapes, pagination and request-count assertions, downloader skip/overwrite/dry-run behaviour, metadata merging, cookie parsing, and every web endpoint including a live download job.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `error: cookie file not found` | `--cookies` points at a file that doesn't exist. Export cookies to `cookies.txt` in the folder you run the command from, or drop the flag. |
| `HTTP 401/403` or “login required” | Export fresh `cookies.txt` and pass `--cookies`. |
| `HTTP 429` / rate limited | Wait a few minutes, raise `--delay`, lower `--workers`. |
| `could not reach www.instagram.com` | Instagram is blocked on your network (VPN, corporate, or a sandbox). Try `--proxy`. |
| `Instagram refused pagination` | Cookies are missing or stale — the first page works anonymously, deep pagination often does not. |
| Some slides missing | The post was edited or a slide was deleted; re-run with `--overwrite`. |
| Videos won't play | The CDN URL expired mid-download — re-run; media URLs are signed and time-limited. |

## License

MIT.

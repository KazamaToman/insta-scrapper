"""Offline tests for the scraper core. Run with: python -m unittest discover -s tests"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from instascrapper import Instagram
from instascrapper import client as client_module
from instascrapper.client import _human_to_int, _post_from_payload, load_cookies, normalise_username, shortcode_from_url
from instascrapper.downloader import Downloader
from instascrapper.errors import NotFoundError, PrivateAccountError
from instascrapper.models import MediaItem, Post, Profile, extension_for
from instascrapper.parsers import parse_feed_item, parse_graph_media, parse_profile
from instascrapper.store import Store, safe_name
from tests import fake_instagram as fake

FIXTURE_DIR = Path(__file__).parent / "fixtures"


# --------------------------------------------------------------------- parsers
class ParserTests(unittest.TestCase):
    def test_profile_payload(self):
        profile = parse_profile(fake.sample_profile())
        self.assertEqual(profile.username, "fakeuser")
        self.assertEqual(profile.full_name, "Fake User")
        self.assertEqual(profile.followers, 2500)
        self.assertEqual(profile.following, 133)
        self.assertEqual(profile.posts_count, 15)
        self.assertTrue(profile.is_verified)
        self.assertFalse(profile.is_private)
        self.assertEqual(profile.profile_pic_url, "https://cdn.example.com/avatar.jpg")
        self.assertTrue(profile.has_next_page)
        self.assertEqual(profile.end_cursor, "CURSOR_1")
        self.assertEqual(len(profile.posts), 2)

    def test_carousel_post(self):
        post = parse_profile(fake.sample_profile()).posts[0]
        self.assertEqual(post.shortcode, "AAA111")
        self.assertEqual(post.media_type, "carousel")
        self.assertEqual(post.media_count, 3)
        self.assertEqual([item.media_type for item in post.media], ["image", "video", "image"])
        self.assertEqual([item.index for item in post.media], [1, 2, 3])
        self.assertTrue(post.media[1].url.endswith(".mp4"))
        self.assertIn("caption for AAA111", post.caption)
        self.assertEqual(post.like_count, 1234)
        self.assertEqual(post.comment_count, 12)
        self.assertEqual(post.owner_username, "fakeuser")
        self.assertEqual(post.date_str, "2023-11-14")
        self.assertTrue(post.thumbnail_url.endswith(".jpg"))

    def test_video_post_has_only_video_media(self):
        post = parse_profile(fake.sample_profile()).posts[1]
        self.assertEqual(post.media_type, "video")
        self.assertTrue(post.has_video)
        self.assertEqual(len(post.media), 1)
        self.assertEqual(post.media[0].media_type, "video")
        self.assertAlmostEqual(post.media_count, 1)

    def test_feed_item_picks_highest_resolution(self):
        post = parse_feed_item(fake.sample_page(2)["items"][0])
        self.assertEqual(post.shortcode, "CCC333")
        self.assertEqual(post.media_type, "image")
        self.assertEqual(post.media[0].width, 1080)
        self.assertEqual(post.media[0].height, 1350)
        self.assertTrue(post.media[0].url.endswith("CCC333.jpg"))

    def test_feed_video_item(self):
        post = parse_feed_item(fake.sample_page(2)["items"][1])
        self.assertEqual(post.media_type, "video")
        self.assertEqual(post.media[0].media_type, "video")
        self.assertTrue(post.media[0].url.endswith(".mp4"))
        self.assertTrue(post.thumbnail_url.endswith(".jpg"))

    def test_missing_fields_do_not_crash(self):
        post = parse_graph_media({"shortcode": "MINIMAL"})
        self.assertEqual(post.shortcode, "MINIMAL")
        self.assertEqual(post.media, [])
        self.assertEqual(post.like_count, 0)

    def test_extension_for(self):
        self.assertEqual(extension_for("https://x/y.jpg?ig_cache=1"), ".jpg")
        self.assertEqual(extension_for("https://x/y.mp4"), ".mp4")
        self.assertEqual(extension_for("https://x/no-extension", "video"), ".mp4")
        self.assertEqual(extension_for("https://x/no-extension"), ".jpg")

    def test_post_round_trip(self):
        post = parse_profile(fake.sample_profile()).posts[0]
        restored = Post.from_dict(post.to_dict())
        self.assertEqual(restored.shortcode, post.shortcode)
        self.assertEqual(restored.media_count, post.media_count)
        self.assertEqual(restored.media[1].url, post.media[1].url)
        self.assertEqual(restored.thumbnail_url, post.thumbnail_url)


# --------------------------------------------------------------------- helpers
class HelperTests(unittest.TestCase):
    def test_normalise_username(self):
        self.assertEqual(normalise_username("@nasa"), "nasa")
        self.assertEqual(normalise_username(" nasa "), "nasa")
        self.assertEqual(normalise_username("https://www.instagram.com/nasa/"), "nasa")
        self.assertEqual(normalise_username("https://instagram.com/nasa?hl=en"), "nasa")
        with self.assertRaises(ValueError):
            normalise_username("not a username!")
        with self.assertRaises(ValueError):
            normalise_username("https://instagram.com/p/C7xYz/")

    def test_shortcode_from_url(self):
        self.assertEqual(shortcode_from_url("https://www.instagram.com/p/C7xYzAbCdEf/"), "C7xYzAbCdEf")
        self.assertEqual(shortcode_from_url("https://www.instagram.com/reel/C7xYzAbCdEf/?hl=en"), "C7xYzAbCdEf")
        self.assertEqual(shortcode_from_url("C7xYzAbCdEf"), "C7xYzAbCdEf")
        with self.assertRaises(ValueError):
            shortcode_from_url("https://example.com/nothing")

    def test_load_cookies_netscape(self):
        content = (
            "# Netscape HTTP Cookie File\n"
            ".instagram.com\tTRUE\t/\tTRUE\t1800000000\tsessionid\tabc123\n"
            "#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t1800000000\tcsrftoken\ttok456\n"
            "instagram.com\tFALSE\t/\tFALSE\t0\tds_user_id\t789\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write(content)
            path = handle.name
        try:
            cookies = load_cookies(path)
        finally:
            os.unlink(path)
        self.assertEqual(cookies["sessionid"], "abc123")
        self.assertEqual(cookies["csrftoken"], "tok456")
        self.assertEqual(cookies["ds_user_id"], "789")

    def test_load_cookies_json(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump({"sessionid": "xyz", "csrftoken": "t"}, handle)
            path = handle.name
        try:
            self.assertEqual(load_cookies(path), {"sessionid": "xyz", "csrftoken": "t"})
        finally:
            os.unlink(path)

    def test_missing_cookie_file(self):
        with self.assertRaises(FileNotFoundError):
            load_cookies("/tmp/definitely-not-here-cookies.txt")

    def test_sessionid_env_var(self):
        os.environ["IG_SESSIONID"] = "env-session"
        try:
            self.assertEqual(load_cookies(None)["sessionid"], "env-session")
        finally:
            del os.environ["IG_SESSIONID"]

    def test_human_to_int(self):
        self.assertEqual(_human_to_int("1,234"), 1234)
        self.assertEqual(_human_to_int("2.5K"), 2500)
        self.assertEqual(_human_to_int("1.2M"), 1_200_000)
        self.assertEqual(_human_to_int("junk"), 0)

    def test_post_from_legacy_payload_shapes(self):
        feed_shape = {"items": [fake.feed_item("LEGACY1")]}
        self.assertEqual(_post_from_payload(feed_shape).shortcode, "LEGACY1")
        graph_shape = {"graphql": {"shortcode_media": fake.graph_post("LEGACY2")}}
        self.assertEqual(_post_from_payload(graph_shape).shortcode, "LEGACY2")
        self.assertIsNone(_post_from_payload({"unexpected": True}))

    def test_safe_name(self):
        self.assertEqual(safe_name("hello world"), "hello_world")
        self.assertEqual(safe_name("../../etc/passwd"), "etc_passwd")
        self.assertEqual(safe_name(""), "unknown")


# ------------------------------------------------------- client against fake IG
class ClientTests(unittest.TestCase):
    def setUp(self):
        self.fake = fake.FakeInstagram().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        base = self.fake.base
        fake.configure(base)

        self._original = {
            "WEB_PROFILE_INFO": client_module.WEB_PROFILE_INFO,
            "FEED_USER": client_module.FEED_USER,
        }
        client_module.WEB_PROFILE_INFO = f"{base}/api/v1/users/web_profile_info/"
        client_module.FEED_USER = base + "/api/v1/feed/user/{pk}/"
        self.addCleanup(self._restore_endpoints)

        self.ig = Instagram(delay=0, retries=1)

    def _restore_endpoints(self):
        for key, value in self._original.items():
            setattr(client_module, key, value)

    def test_get_profile(self):
        profile = self.ig.get_profile("@fakeuser")
        self.assertEqual(profile.username, "fakeuser")
        self.assertEqual(profile.id, "123")
        self.assertTrue(profile.has_next_page)
        self.assertEqual(self.ig.request_count, 1)

    def test_profile_not_found(self):
        with self.assertRaises(NotFoundError):
            self.ig.get_profile("nosuchuser")

    def test_pagination_collects_everything_once(self):
        profile = self.ig.get_profile("fakeuser")
        posts = list(self.ig.iter_posts(profile))
        self.assertEqual([post.shortcode for post in posts], ["AAA111", "BBB222", "CCC333", "DDD444", "EEE555"])
        self.assertEqual(self.ig.request_count, 3)  # profile + 2 feed pages

    def test_limit_avoids_extra_requests(self):
        profile = self.ig.get_profile("fakeuser")
        posts = list(self.ig.iter_posts(profile, limit=1))
        self.assertEqual(len(posts), 1)
        self.assertEqual(self.ig.request_count, 1)

    def test_limit_across_pages(self):
        profile = self.ig.get_profile("fakeuser")
        posts = list(self.ig.iter_posts(profile, limit=4))
        self.assertEqual(len(posts), 4)
        self.assertEqual(self.ig.request_count, 2)

    def test_private_account_raises(self):
        profile = Profile(id="1", username="secret", is_private=True)
        with self.assertRaises(PrivateAccountError):
            list(self.ig.iter_posts(profile))


# ------------------------------------------------------------------ downloader
class DownloaderTests(unittest.TestCase):
    def setUp(self):
        self.fake = fake.FakeInstagram().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        base = self.fake.base
        fake.configure(base)
        self._original = {
            "WEB_PROFILE_INFO": client_module.WEB_PROFILE_INFO,
            "FEED_USER": client_module.FEED_USER,
        }
        client_module.WEB_PROFILE_INFO = f"{base}/api/v1/users/web_profile_info/"
        client_module.FEED_USER = base + "/api/v1/feed/user/{pk}/"
        self.addCleanup(self._restore)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ig = Instagram(delay=0, retries=1)
        self.events = []

    def _restore(self):
        for key, value in self._original.items():
            setattr(client_module, key, value)

    def _downloader(self, **kwargs):
        return Downloader(
            self.tmp.name,
            client=self.ig,
            workers=2,
            on_event=self.events.append,
            **kwargs,
        )

    def test_download_account_writes_files_and_metadata(self):
        summary = self._downloader().download_account("fakeuser", limit=2)
        account_dir = Path(self.tmp.name) / "fakeuser"

        names = sorted(entry.name for entry in account_dir.iterdir())
        self.assertIn("metadata.json", names)
        self.assertIn("posts.csv", names)
        self.assertIn("profile.jpg", names)
        # carousel: 2 images + 1 video; video post: 1 video
        media_files = [name for name in names if name.endswith((".jpg", ".png", ".mp4")) and name != "profile.jpg"]
        self.assertEqual(len(media_files), 4)
        self.assertTrue(any(name.endswith("_01.jpg") for name in media_files))
        self.assertTrue(any(name.endswith(".mp4") for name in media_files))

        self.assertEqual(len(summary.failed), 0)
        self.assertEqual(len(summary.downloaded), 5)  # 4 media + profile picture
        self.assertGreater(summary.total_bytes, 0)

        metadata = json.loads((account_dir / "metadata.json").read_text())
        self.assertEqual(metadata["username"], "fakeuser")
        self.assertEqual(metadata["profile"]["followers"], 2500)
        self.assertEqual(metadata["post_count"], 2)
        self.assertEqual(metadata["posts"][0]["shortcode"], "AAA111")

        csv_rows = (account_dir / "posts.csv").read_text().strip().splitlines()
        self.assertEqual(len(csv_rows), 3)  # header + 2 posts

        event_types = [event["type"] for event in self.events]
        self.assertIn("account", event_types)
        self.assertIn("file", event_types)
        self.assertIn("done", event_types)

    def test_second_run_skips_existing_files(self):
        self._downloader().download_account("fakeuser", limit=2)
        self.events.clear()
        summary = self._downloader().download_account("fakeuser", limit=2)
        self.assertEqual(len(summary.downloaded), 0)
        self.assertEqual(len(summary.skipped), 5)

    def test_overwrite_redownloads(self):
        self._downloader().download_account("fakeuser", limit=1)
        summary = self._downloader(overwrite=True).download_account("fakeuser", limit=1)
        self.assertEqual(len(summary.skipped), 0)
        self.assertEqual(len(summary.downloaded), len(summary.files))

    def test_dry_run_writes_nothing(self):
        summary = self._downloader(dry_run=True).download_account("fakeuser", limit=2)
        media = [entry for entry in (Path(self.tmp.name) / "fakeuser").iterdir() if entry.suffix != ".json" and entry.suffix != ".csv"]
        self.assertEqual(media, [])
        self.assertTrue(summary.files)
        self.assertTrue(all(result.status == "planned" for result in summary.files))

    def test_no_videos_option(self):
        summary = self._downloader(include_videos=False).download_account("fakeuser", limit=2)
        mp4 = [result for result in summary.files if result.filename.endswith(".mp4")]
        self.assertEqual(mp4, [])
        self.assertTrue(all(result.status != "failed" for result in summary.files))

    def test_single_post_download(self):
        post = parse_profile(fake.PROFILE).posts[1]  # retargeted at the running fake server
        summary = self._downloader().download_single_post(post, username="fakeuser")
        self.assertEqual(len(summary.failed), 0)
        self.assertTrue(any(result.filename.endswith(".mp4") for result in summary.files))

    def test_failed_download_is_reported_not_raised(self):
        post = Post(
            shortcode="BROKEN1",
            taken_at=1_700_000_000,
            media=[MediaItem(index=1, media_type="image", url=f"{self.fake.base}/broken/x.jpg")],
            owner_username="fakeuser",
        )
        summary = self._downloader().download_single_post(post, username="fakeuser")
        self.assertEqual(len(summary.failed), 1)
        leftover = list((Path(self.tmp.name) / "fakeuser").glob("*.part"))
        self.assertEqual(leftover, [])


# ---------------------------------------------------------------------- store
class StoreTests(unittest.TestCase):
    def test_media_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            post = Post(
                shortcode="ABC123",
                taken_at=1_700_000_000,
                media=[MediaItem(index=1, media_type="image", url="https://x/a.jpg")],
            )
            paths = store.paths("NASA")
            self.assertEqual(paths.media(post, post.media[0]).name, "2023-11-14_ABC123.jpg")

    def test_carousel_paths_are_numbered(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            post = Post(
                shortcode="ABC123",
                taken_at=1_700_000_000,
                media=[
                    MediaItem(index=1, media_type="image", url="https://x/a.jpg"),
                    MediaItem(index=2, media_type="video", url="https://x/b.mp4"),
                ],
            )
            paths = store.paths("nasa")
            self.assertEqual(paths.media(post, post.media[0]).name, "2023-11-14_ABC123_01.jpg")
            self.assertEqual(paths.media(post, post.media[1]).name, "2023-11-14_ABC123_02.mp4")

    def test_metadata_merges_across_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            profile = Profile(id="1", username="nasa", full_name="NASA", followers=10)
            first = Post(shortcode="ONE", taken_at=1_700_000_000)
            second = Post(shortcode="TWO", taken_at=1_700_000_100)
            store.save_metadata(profile, [first])
            store.save_metadata(profile, [second])
            data = store.load_metadata("nasa")
            self.assertEqual(sorted(post["shortcode"] for post in data["posts"]), ["ONE", "TWO"])
            self.assertEqual(data["post_count"], 2)
            # newest first
            self.assertEqual(data["posts"][0]["shortcode"], "TWO")

    def test_library_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(tmp)
            profile = Profile(id="1", username="nasa", full_name="NASA")
            store.save_metadata(profile, [Post(shortcode="ONE", taken_at=1_700_000_000)])
            (store.paths("nasa").directory / "2023-11-14_ONE.jpg").write_bytes(b"x" * 32)
            accounts = store.list_accounts()
            self.assertEqual(len(accounts), 1)
            self.assertEqual(accounts[0]["username"], "nasa")
            self.assertEqual(accounts[0]["bytes"], 32)
            library = store.library("nasa")
            self.assertEqual(len(library["files"]), 1)
            self.assertEqual(library["files"][0]["type"], "image")


if __name__ == "__main__":
    unittest.main()

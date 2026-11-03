"""Tests for the FastAPI layer (no network access required)."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from instascrapper import client as client_module
from instascrapper.errors import NetworkError
from instascrapper.web.app import create_app
from tests import fake_instagram as fake


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = create_app(download_dir=self.tmp.name, delay=0)
        self.client = TestClient(self.app)

    # ------------------------------------------------------------------ basics
    def test_index_and_static_assets(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("instascrapper", page.text)
        self.assertIn("/static/app.js", page.text)
        self.assertEqual(self.client.get("/static/app.js").status_code, 200)
        self.assertEqual(self.client.get("/static/style.css").status_code, 200)

    def test_health(self):
        payload = self.client.get("/api/health").json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["authenticated"])
        self.assertTrue(payload["demo_available"])

    def test_demo_payload(self):
        payload = self.client.get("/api/demo").json()
        self.assertTrue(payload["demo"])
        self.assertEqual(payload["profile"]["username"], "demo.studio")
        self.assertEqual(len(payload["posts"]), 8)
        first = payload["posts"][0]
        self.assertEqual(first["media_type"], "carousel")
        self.assertEqual(len(first["media"]), 3)
        self.assertTrue(first["thumbnail_url"].startswith("/demo/"))

    def test_demo_assets_are_served(self):
        self.assertEqual(self.client.get("/demo/p1.png").status_code, 200)
        self.assertEqual(self.client.get("/static/demo/avatar.png").status_code, 200)

    # ------------------------------------------------------------------ errors
    def test_invalid_username(self):
        response = self.client.get("/api/profile/not%20a%20username")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["kind"], "bad_request")

    def test_network_failure_is_reported(self):
        def boom(*args, **kwargs):
            raise NetworkError("simulated outage")

        self.app.state.client.get_profile = boom
        response = self.client.get("/api/profile/someone")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["kind"], "network")

    def test_unknown_job(self):
        self.assertEqual(self.client.get("/api/jobs/nope").status_code, 404)
        self.assertEqual(self.client.get("/api/jobs/nope/events").status_code, 404)
        self.assertEqual(self.client.post("/api/jobs/nope/cancel").status_code, 404)

    def test_library_empty(self):
        payload = self.client.get("/api/library").json()
        self.assertEqual(payload["accounts"], [])
        self.assertEqual(self.client.get("/api/library/nobody").status_code, 404)

    # ------------------------------------------------------- download pipeline
    def test_download_job_end_to_end(self):
        with fake.FakeInstagram() as server:
            fake.configure(server.base)
            original = (client_module.WEB_PROFILE_INFO, client_module.FEED_USER)
            client_module.WEB_PROFILE_INFO = f"{server.base}/api/v1/users/web_profile_info/"
            client_module.FEED_USER = server.base + "/api/v1/feed/user/{pk}/"
            try:
                response = self.client.post(
                    "/api/download",
                    json={"username": "fakeuser", "shortcodes": ["AAA111"], "include_videos": True},
                )
                self.assertEqual(response.status_code, 200)
                job_id = response.json()["job_id"]
                snapshot = self._wait_for(job_id)
            finally:
                client_module.WEB_PROFILE_INFO, client_module.FEED_USER = original

        self.assertEqual(snapshot["status"], "done", snapshot.get("error"))
        self.assertGreaterEqual(snapshot["counters"]["downloaded"], 3)  # carousel 3 + profile pic

        account_dir = Path(self.tmp.name) / "fakeuser"
        self.assertTrue((account_dir / "metadata.json").exists())
        self.assertTrue((account_dir / "posts.csv").exists())
        self.assertTrue((Path(self.tmp.name) / "fakeuser" / "profile.jpg").exists())

        library = self.client.get("/api/library").json()
        self.assertEqual(library["accounts"][0]["username"], "fakeuser")

        detail = self.client.get("/api/library/fakeuser").json()
        self.assertTrue(detail["files"])
        served = self.client.get(detail["files"][0]["url"])
        self.assertEqual(served.status_code, 200)
        self.assertGreater(len(served.content), 0)

    def test_download_requires_username(self):
        response = self.client.post("/api/download", json={"shortcodes": ["AAA"]})
        self.assertEqual(response.status_code, 422)

    def _wait_for(self, job_id: str, timeout: float = 20.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            snapshot = self.client.get(f"/api/jobs/{job_id}").json()
            if snapshot["status"] in {"done", "error", "cancelled"}:
                return snapshot
            time.sleep(0.05)
        self.fail("job did not finish in time")


if __name__ == "__main__":
    unittest.main()

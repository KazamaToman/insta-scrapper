"""CLI behaviour tests (no network access)."""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from instascrapper.cli import build_parser, main


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def test_help_lists_commands(self):
        parser = build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["--help"])

    def test_missing_cookie_file_is_a_clean_error(self):
        code, _, err = run(["profile", "nasa", "--cookies", "/tmp/nope-cookies.txt"])
        self.assertEqual(code, 2)
        self.assertIn("cookie file not found", err)
        self.assertIn("hint:", err)
        self.assertNotIn("Traceback", err)

    def test_invalid_username_is_a_clean_error(self):
        code, _, err = run(["profile", "not a username"])
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)

    def test_network_failure_is_a_clean_error(self):
        # Port 9 (discard) is closed, so this fails fast without touching Instagram.
        code, _, err = run(["posts", "nasa", "--limit", "1", "--proxy", "http://127.0.0.1:9"])
        self.assertEqual(code, 1)
        self.assertIn("could not reach", err)
        self.assertIn("hint:", err)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()

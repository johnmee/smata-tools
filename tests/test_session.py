"""Offline tests for the cached login token and login URL handling."""

import json
import stat
import tempfile
import unittest
from pathlib import Path

from smata.session import (
    clear_cached_session,
    load_cached_session,
    normalise_login_url,
    save_cached_session,
)

URL = "https://acme.my.smata.com/users/sign_in"


class LoginUrlTests(unittest.TestCase):
    def test_login_url_normalised(self):
        for u in ("acme.my.smata.com", "https://acme.my.smata.com/", URL):
            self.assertEqual(normalise_login_url(u), URL)


class SessionCacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "cache" / "session.json"

    def test_save_then_load(self):
        save_cached_session(self.path, "acme.my.smata.com", "Me@Example.com", "tok")
        entry = load_cached_session(self.path, URL, "me@example.com")
        self.assertEqual(entry["access_token"], "tok")

    def test_saved_file_is_owner_only(self):
        save_cached_session(self.path, URL, "me@example.com", "tok")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_tokens_are_kept_per_user(self):
        save_cached_session(self.path, URL, "a@example.com", "tok-a")
        save_cached_session(self.path, URL, "b@example.com", "tok-b")
        self.assertEqual(load_cached_session(self.path, URL, "a@example.com")["access_token"], "tok-a")
        self.assertEqual(load_cached_session(self.path, URL, "b@example.com")["access_token"], "tok-b")

    def test_clear_removes_only_that_user(self):
        save_cached_session(self.path, URL, "a@example.com", "tok-a")
        save_cached_session(self.path, URL, "b@example.com", "tok-b")
        clear_cached_session(self.path, URL, "b@example.com")
        self.assertIsNone(load_cached_session(self.path, URL, "b@example.com"))
        self.assertEqual(load_cached_session(self.path, URL, "a@example.com")["access_token"], "tok-a")

    def test_missing_or_corrupt_file_loads_nothing(self):
        self.assertIsNone(load_cached_session(self.path, URL, "me@example.com"))
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{not json")
        self.assertIsNone(load_cached_session(self.path, URL, "me@example.com"))

    def test_no_temporary_files_are_left_behind(self):
        save_cached_session(self.path, URL, "me@example.com", "tok")
        clear_cached_session(self.path, URL, "me@example.com")
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["session.json"])
        json.loads(self.path.read_text())


if __name__ == "__main__":
    unittest.main()

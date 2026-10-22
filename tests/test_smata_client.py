"""Offline tests for the Client.

Every test talks to FakeSmata (tests/fakes.py), which stands in for the HTTP layer and mimics
the SMATA endpoints documented in smata-api.md.  OfflineTestCase fails any request that
escapes the fake, so nothing can reach the real SMATA host.
"""

import unittest

import requests

from smata.client import Client, normalise_login_url
from smata.errors import ApiError, AuthError
from tests.fakes import TENANT, OfflineTestCase, doc


class GuardTests(OfflineTestCase):
    def test_unattached_client_cannot_reach_the_network(self):
        client = Client(TENANT, "UTC", min_interval=0, retries=0)
        with self.assertRaises(AssertionError):
            client.login("me@example.com", "pw")


class LoginUrlTests(unittest.TestCase):
    def test_login_url_normalised(self):
        for u in ("acme.my.smata.com", "https://acme.my.smata.com/", "https://acme.my.smata.com/users/sign_in"):
            self.assertEqual(normalise_login_url(u), "https://acme.my.smata.com/users/sign_in")


class LoginTests(OfflineTestCase):
    def test_login_then_api_call(self):
        server, client = self.make([doc(1)])
        client.login(server.email, server.password)
        self.assertEqual(client.get_json("/v1/documents/1")["id"], 1)

    def test_wrong_password_is_auth_error(self):
        server, client = self.make()
        with self.assertRaisesRegex(AuthError, "login failed"):
            client.login(server.email, "wrong")

    def test_page_without_csrf_token_is_auth_error(self):
        server, client = self.make()
        server.login_page = "<html>not a SMATA login page</html>"
        with self.assertRaisesRegex(AuthError, "authenticity_token"):
            client.login(server.email, server.password)

    def test_api_call_before_login_is_auth_error(self):
        _, client = self.make([doc(1)])
        with self.assertRaises(AuthError):
            client.get_json("/v1/documents/1")

    def test_rejected_session_logs_in_again(self):
        server, client = self.logged_in([doc(1)])
        server.expire_tokens()
        self.assertEqual(client.get_json("/v1/documents/1")["id"], 1)
        self.assertEqual(server.logins, 2)


class HeaderTests(OfflineTestCase):
    def test_accept_json_only_on_api_calls(self):
        server, client = self.logged_in([doc(1)])
        client.get_json("/v1/documents/1")
        by_kind = {"login": [], "api": []}
        for _, url, headers in server.requests:
            by_kind["login" if url.endswith("/users/sign_in") else "api"].append(headers)
        for headers in by_kind["login"]:
            self.assertNotIn("Accept", headers)
        self.assertEqual(by_kind["api"][-1]["Accept"], "application/json")
        self.assertEqual(by_kind["api"][-1]["Authorization"], "Bearer tok-1")

    def test_bearer_token_not_sent_to_file_host(self):
        server, client = self.logged_in()
        url = "https://s3.example/f.pdf"
        server.files[url] = b"data"
        client.download(url, self.tmp / "f.pdf")
        self.assertEqual((self.tmp / "f.pdf").read_bytes(), b"data")
        (_, _, headers), = [r for r in server.requests if r[1] == url]
        self.assertNotIn("Authorization", headers)
        self.assertNotIn("Authorization", client._files.headers)


class PaginationTests(OfflineTestCase):
    def test_paginate_walks_every_page_for_each_pass(self):
        docs = [doc(i) for i in range(1, 6)] + [doc(10, is_invoice=True)]
        server, client = self.logged_in(docs)
        plain = [d["id"] for d in client.paginate("/v1/documents", "documents", per_page=2, is_invoice="false")]
        invoices = [d["id"] for d in client.paginate("/v1/documents", "documents", per_page=2, is_invoice="true")]
        self.assertEqual(plain, [1, 2, 3, 4, 5])
        self.assertEqual(invoices, [10])
        self.assertEqual(server.count("GET", "/api/v1/documents"), 4)


class RetryTests(OfflineTestCase):
    def test_server_errors_are_retried(self):
        server, client = self.logged_in([doc(1)])
        server.fail("/v1/documents/1", [503, 429])
        self.assertEqual(client.get_json("/v1/documents/1")["id"], 1)
        self.assertEqual(server.count("GET", "/v1/documents/1"), 3)

    def test_retries_exhausted_is_api_error(self):
        server, client = self.logged_in([doc(1)], retries=2)
        server.fail("/v1/documents/1", [503] * 5)
        with self.assertRaises(ApiError) as cm:
            client.get_json("/v1/documents/1")
        self.assertEqual(cm.exception.status, 503)
        self.assertEqual(server.count("GET", "/v1/documents/1"), 3)

    def test_not_found_is_not_retried(self):
        server, client = self.logged_in()
        with self.assertRaises(ApiError) as cm:
            client.get_json("/v1/documents/99")
        self.assertEqual(cm.exception.status, 404)
        self.assertEqual(server.count("GET", "/v1/documents/99"), 1)


class DownloadTests(OfflineTestCase):
    def test_interrupted_download_leaves_no_partial_file(self):
        server, client = self.logged_in()
        url = "https://s3.example/broken.pdf"
        server.broken_files.add(url)
        with self.assertRaises(requests.ConnectionError):
            client.download(url, self.tmp / "docs" / "broken.pdf")
        self.assertEqual(list((self.tmp / "docs").iterdir()), [])

    def test_missing_file_is_api_error(self):
        _, client = self.logged_in()
        with self.assertRaises(ApiError) as cm:
            client.download("https://s3.example/gone.pdf", self.tmp / "gone.pdf")
        self.assertEqual(cm.exception.status, 404)
        self.assertFalse((self.tmp / "gone.pdf").exists())


class CompatTests(unittest.TestCase):
    """The package re-export keeps the old one-module import style working."""

    def test_smata_archive_is_an_alias(self):
        from smata.client import Client as Direct

        self.assertIs(Direct, Client)

    def test_public_api_is_importable_from_the_package(self):
        from smata import Archive, load_config, open_store, resolve_config
        from smata import Client as PackageClient

        self.assertIs(PackageClient, Client)
        self.assertTrue(callable(Archive))
        self.assertTrue(callable(load_config))
        self.assertTrue(callable(resolve_config))
        self.assertTrue(callable(open_store))


if __name__ == "__main__":
    unittest.main()

"""Fakes shared by the offline test files.

FakeSmata stands in for the HTTP layer: the SMATA login form, JSON API and public file host.
OfflineTestCase wires a real Client to it and makes any request that escapes the fake
fail the test, so nothing can reach the real SMATA host.
"""

import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import requests

from smata.archive import deep_merge
from smata.client import Client

T0 = 1_700_000_000  # 2023-11-14
TENANT = "https://acme.my.smata.com"
API_BASE = "https://my.smata.com/api"
FILE_HOST = "https://files.example/"
LOGIN_PAGE = '<form><input name="authenticity_token" value="csrf-123"></form>'


def doc(id, category="Minutes", is_invoice=False, updated_at=T0, **extra):
    d = {"id": id, "category": category, "is_invoice": is_invoice, "file_extension": "pdf",
         "added_date": T0, "created_at": T0, "updated_at": updated_at, "display_name": f"Doc {id}",
         "links": {"file_url": f"{FILE_HOST}{id}/f.pdf"}}
    d.update(extra)
    return d


class FakeResponse:
    def __init__(self, status=200, *, body=None, text="", headers=None, chunks=(b"",), fail_after_chunks=False):
        self.status_code = status
        self.headers = headers or {}
        self.text = text
        self._body = body
        self._chunks = chunks
        self._fail = fail_after_chunks

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body

    def iter_content(self, chunk_size):
        yield from self._chunks
        if self._fail:
            raise requests.ConnectionError("connection dropped mid-download")

    def close(self):
        pass


class FakeSmata:
    """The SMATA login form, JSON API and public file host, as seen over HTTP.

    Behaviour copied from smata-api.md:
    * Login is a Rails form.  A wrong password re-renders the form with 200 and sets no cookie.
    * A successful login sets an access_token cookie, sent back as a Bearer token.
    * API calls without a valid token get 401.
    * List endpoints paginate with page/per_page and report meta.total_pages.
    * The list omits fields only the detail has; `details` holds those extras per document id.
    * File URLs live on another host and need no credentials.  Any URL under FILE_HOST is
      served, with its own URL as the content, unless it is in `files`, `broken_files` or `missing_files`.
    """

    def __init__(self, docs=(), email="me@example.com", password="pw"):
        self.docs = list(docs)
        self.details: dict[int, dict] = {}
        self.missing_detail: set[int] = set()
        self.failing_lists: set[str] = set()       # is_invoice values ("true"/"false") whose listing returns 500
        self.files: dict[str, bytes] = {}
        self.broken_files: set[str] = set()
        self.missing_files: set[str] = set()
        self.email = email
        self.password = password
        self.login_page = LOGIN_PAGE
        self.requests: list[tuple[str, str, dict]] = []   # (method, url, headers)
        self.logins = 0
        self._valid_tokens: set[str] = set()
        self._failures: dict[str, list[int]] = {}
        self._session: requests.Session | None = None

    def attach(self, client: Client) -> Client:
        self._session = client._api
        setattr(client._api, "request", self._api_request)  # noqa: B010
        setattr(client._files, "request", self._file_request)  # noqa: B010
        return client

    def expire_tokens(self):
        self._valid_tokens.clear()

    def fail(self, path_suffix: str, statuses: list[int]):
        """The next len(statuses) API calls ending in path_suffix return these statuses."""
        self._failures[path_suffix] = list(statuses)

    def count(self, method: str, url_part: str) -> int:
        return sum(1 for m, u, _ in self.requests if m == method and url_part in u)

    def detail_requests(self) -> list[int]:
        """Ids of the documents whose detail was requested, in order."""
        ids = (re.search(r"/v1/documents/(\d+)$", u) for m, u, _ in self.requests if m == "GET")
        return [int(m[1]) for m in ids if m]

    def file_requests(self) -> list[str]:
        return [u for m, u, _ in self.requests if u.startswith(FILE_HOST) or u in self.files]

    def _api_request(self, method, url, headers=None, params=None, data=None, **kw):
        headers = dict(headers or {})
        self.requests.append((method, url, headers))
        if url == f"{TENANT}/users/sign_in":
            return self._login(method, data)
        if not url.startswith(f"{API_BASE}/"):
            raise AssertionError(f"request to unexpected host: {method} {url}")
        path = url.removeprefix(API_BASE)
        for suffix, statuses in self._failures.items():
            if path.endswith(suffix) and statuses:
                return FakeResponse(statuses.pop(0))
        if headers.get("Authorization", "").removeprefix("Bearer ") not in self._valid_tokens:
            return FakeResponse(401, body={"error": "unauthorized"})
        return self._route(path, params or {})

    def _login(self, method, data):
        if method == "GET":
            return FakeResponse(text=self.login_page)
        ok = (data.get("authenticity_token") == "csrf-123"
              and data.get("user[email]") == self.email
              and data.get("user[password]") == self.password)
        if ok:
            self.logins += 1
            token = f"tok-{self.logins}"
            self._valid_tokens.add(token)
            self._session.cookies.set("access_token", token)
        return FakeResponse(text=self.login_page)

    def _route(self, path, params):
        if path == "/v1/documents":
            flag = params.get("is_invoice")
            if flag in self.failing_lists:
                return FakeResponse(500)
            rows = [d for d in self.docs if bool(d.get("is_invoice")) == (flag == "true")]
            per_page, page = int(params.get("per_page", 25)), int(params.get("page", 1))
            pages = max(1, -(-len(rows) // per_page))
            chunk = rows[(page - 1) * per_page: page * per_page]
            return FakeResponse(body={"documents": chunk, "meta": {"total_pages": pages, "page": page}})
        m = re.fullmatch(r"/v1/documents/(\d+)", path)
        if m:
            id = int(m[1])
            if id in self.missing_detail:
                return FakeResponse(404, body={"error": "Attachment Not Found"})
            for d in self.docs:
                if d["id"] == id:
                    return FakeResponse(body=deep_merge(d, self.details.get(id, {})))
            return FakeResponse(404, body={"error": "not found"})
        raise AssertionError(f"FakeSmata has no route for {path}")

    def _file_request(self, method, url, headers=None, **kw):
        self.requests.append((method, url, dict(headers or {})))
        if url in self.broken_files:
            return FakeResponse(chunks=(b"par",), fail_after_chunks=True)
        if url in self.files:
            return FakeResponse(chunks=(self.files[url],))
        if url.startswith(FILE_HOST) and url not in self.missing_files:
            return FakeResponse(chunks=(url.encode(),))
        return FakeResponse(404)


class OfflineTestCase(unittest.TestCase):
    """Blocks real network access and sleeping, and provides a temp dir (self.tmp) and a client factory."""

    def setUp(self):
        guard = mock.patch.object(requests.Session, "request", side_effect=AssertionError("real network request"))
        guard.start()
        self.addCleanup(guard.stop)
        sleep = mock.patch.object(time, "sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self._cache_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._cache_tmp.cleanup)
        self.session_file = Path(self._cache_tmp.name) / "session.json"
        session_patch = mock.patch("smata.client.DEFAULT_SESSION_PATH", self.session_file)
        session_patch.start()
        self.addCleanup(session_patch.stop)

    def make(self, docs=(), **client_kw):
        server = FakeSmata(docs)
        client = server.attach(Client(TENANT, "UTC", min_interval=0, **client_kw))
        return server, client

    def logged_in(self, docs=(), **client_kw):
        server, client = self.make(docs, **client_kw)
        client.login(server.email, server.password)
        return server, client

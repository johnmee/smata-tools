"""The SMATA HTTP client: login, JSON calls, pagination and file downloads.

There is no API key or OAuth flow.  The session is the web login form: fetch it for a
Rails CSRF token, POST the credentials, and read the `access_token` cookie it sets in
reply.  That token is sent as a bearer header on every API call.  Tokens are cached in
~/.cache/smata/session.json and reused across runs until expired or rejected.  See
smata-api.md sections 2 and 18.

Only GET requests are ever issued.
"""
from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import requests

from smata.errors import ApiError, AuthError
from smata.session import (
    DEFAULT_SESSION_PATH,
    clear_cached_session,
    load_cached_session,
    normalise_login_url,
    save_cached_session,
)
from smata.version import __version__

log = logging.getLogger("smata")

#: Every path in smata-api.md is relative to this base.
API_BASE = "https://my.smata.com/api"

USER_AGENT = f"smata-archive/{__version__} (+https://github.com/)"

#: The form field names and values the login POST sends.  `authenticity_token` is filled in.
LOGIN_FORM = {
    "utf8": "✓",
    "user[remember_me]": "0",
    "commit": "Log in",
}

_DEFAULT_SESSION = object()


class SmataClient:
    """A client session with the SMATA JSON API.

    The bearer token is sent only to the API host. Files are fetched with a
    separate session that carries no credentials, because file URLs point at
    public S3 buckets.
    """

    def __init__(self,
        login_url: str,
        timezone: str,
        *,
        api_base: str = API_BASE,
        min_interval: float = 0.25,
        timeout: float = 60,
        retries: int = 5,
        session_path: Path | str | None = _DEFAULT_SESSION,
    ):
        self.login_url = normalise_login_url(login_url)
        self.timezone = timezone
        self.api_base = api_base.rstrip("/")
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        resolved_path = DEFAULT_SESSION_PATH if session_path is _DEFAULT_SESSION else session_path
        self.session_path = Path(resolved_path).expanduser() if resolved_path is not None else None
        self._api = requests.Session()
        self._api.headers.update({"User-Agent": USER_AGENT})
        self._files = requests.Session()
        self._files.headers.update({"User-Agent": USER_AGENT})
        self._token: str | None = None
        self._credentials: tuple[str, str | None] | None = None
        self._last_request = 0.0

    def has_valid_session(self, email: str) -> bool:
        """Returns True if a valid non-expired cached session exists for this user."""
        if not self.session_path:
            return False
        return load_cached_session(self.session_path, self.login_url, email) is not None

    def clear_session(self, email: str | None = None) -> None:
        """Clears in-memory credentials and the cached session file on disk."""
        target_email = email or (self._credentials[0] if self._credentials else None)
        self._token = None
        self._credentials = None
        self._api.cookies.clear()
        if self.session_path and target_email:
            clear_cached_session(self.session_path, self.login_url, target_email)

    def login(self, email: str, password: str | None = None, *, use_cache: bool = True) -> None:
        """Logs in through the web form and keeps the access_token. Raises AuthError."""
        if use_cache and self.session_path:
            cached = load_cached_session(self.session_path, self.login_url, email)
            if cached and cached.get("access_token"):
                self._token = cached["access_token"]
                self._credentials = (email, password)
                self._api.cookies.set("access_token", self._token)
                log.info("using cached session for %s", email)
                return

        if password is None:
            raise AuthError("no password provided and no valid cached session found")

        # The cached path sets a domain-less access_token cookie; a second one from the
        # login response would make cookies.get() raise CookieConflictError.
        self._api.cookies.clear()
        try:
            page = self._request(self._api, "GET", self.login_url, api=False)
        except ApiError as e:
            raise AuthError(f"could not load the login page {self.login_url}: {e}") from e
        match = re.search(r'name="authenticity_token" value="([^"]+)"', page.text)
        if not match:
            raise AuthError(f"no authenticity_token on {self.login_url}; is this a SMATA login URL?")
        form = {
            **LOGIN_FORM,
            "authenticity_token": match.group(1),
            "user[email]": email,
            "user[password]": password,
        }
        try:
            self._request(self._api, "POST", self.login_url, api=False, data=form)
        except ApiError as e:
            raise AuthError(f"login request failed: {e}") from e
        # A failed login re-renders the form with 200, so the cookie is the only signal.
        token = self._api.cookies.get("access_token")
        if not token:
            raise AuthError("login failed: wrong email or password, or two-factor authentication is enabled")
        self._token = token
        self._credentials = (email, password)

        if self.session_path:
            save_cached_session(self.session_path, self.login_url, email, token)

    def get_json(self, path: str, **params) -> Any:
        """GETs an API path (relative to /api, e.g. "/v1/documents") and returns the JSON."""
        url = path if path.startswith("http") else f"{self.api_base}/{path.lstrip('/')}"
        try:
            resp = self._request(self._api, "GET", url, api=True, params=params)
        except AuthError:
            if not self._credentials or not self._credentials[1]:
                if self.session_path and self._credentials:
                    clear_cached_session(self.session_path, self.login_url, self._credentials[0])
                raise
            log.info("session rejected; logging in again")
            if self.session_path:
                clear_cached_session(self.session_path, self.login_url, self._credentials[0])
            self.login(self._credentials[0], self._credentials[1], use_cache=False)
            resp = self._request(self._api, "GET", url, api=True, params=params)
        try:
            return resp.json()
        except ValueError as e:
            raise ApiError(f"GET {path}: response is not JSON", resp.status_code) from e

    def paginate(self, path: str, key: str, per_page: int = 100, **params):
        """Yields every item of a paginated list endpoint."""
        page = 1
        while True:
            data = self.get_json(path, page=page, per_page=per_page, **params)
            yield from data.get(key) or []
            total_pages = (data.get("meta") or {}).get("total_pages") or 1
            if page >= total_pages:
                return
            page += 1

    def download(self, url: str, path: str | os.PathLike) -> int:
        """Streams url to path via path.part, so an interrupted download leaves no partial file.
        Returns the number of bytes written."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        resp = self._request(self._files, "GET", url, api=False, stream=True)
        size = 0
        try:
            with open(part, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    f.write(chunk)
                    size += len(chunk)
            os.replace(part, path)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        finally:
            resp.close()
        return size

    def _request(self,
        session: requests.Session,
        method: str,
        url: str,
        *,
        api: bool,
        **kwargs
    ):
        headers = {"Accept": "application/json"} if api else {}
        if api and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        for attempt in range(self.retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            try:
                resp = session.request(method, url, headers=headers, timeout=self.timeout, **kwargs)
            except requests.RequestException as e:
                if attempt == self.retries:
                    raise ApiError(f"{method} {url}: {e}") from e
                delay = 2 ** attempt
                log.warning("%s %s: %s; retrying in %ss", method, url, e, delay)
                time.sleep(delay)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == self.retries:
                    raise ApiError(f"{method} {url}: HTTP {resp.status_code}", resp.status_code)
                delay = _retry_after(resp) or 2 ** attempt
                log.warning("%s %s: HTTP %s; retrying in %ss", method, url, resp.status_code, delay)
                resp.close()
                time.sleep(delay)
                continue
            if resp.status_code == 401 and api:
                raise AuthError(f"{method} {url}: HTTP 401")
            if resp.status_code >= 400:
                raise ApiError(f"{method} {url}: HTTP {resp.status_code}", resp.status_code)
            return resp
        raise AssertionError("unreachable")


#: Both names for the class: `SmataClient` is the original, `Client` the short form.
Client = SmataClient


def _retry_after(resp: requests.Response) -> float | None:
    try:
        return min(float(resp.headers.get("Retry-After", "")), 300)
    except ValueError:
        return None

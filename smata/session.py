"""The cached login token: where it lives on disk and how it is read, written and cleared.

Tokens are kept in ~/.cache/smata/session.json by default, keyed by login URL and email, so
that later runs can skip the login.  The file is written atomically with 0600 permissions.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("smata")

DEFAULT_SESSION_PATH = Path("~/.cache/smata/session.json").expanduser()


def normalise_login_url(url: str) -> str:
    """Clean up the login url string provided by user configuration.
    """
    url = url.strip().rstrip("/")
    if not url.startswith(("https://", "http://")):
        url = "https://" + url
    if not url.endswith("/users/sign_in"):
        url += "/users/sign_in"
    return url



def _write_private(path: Path, data: dict) -> None:
    """Writes data as JSON to path via a temporary file, readable by the owner only."""
    tmp_file = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    fd = os.open(tmp_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_file, path)
    except BaseException:
        try:
            tmp_file.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _cache_key(login_url: str, email: str) -> str:
    return f"{normalise_login_url(login_url)}|{email.strip().lower()}"


def load_cached_session(session_path: Path | str, login_url: str, email: str) -> dict | None:
    """Reads a session from the cache file if present and valid."""
    path = Path(session_path).expanduser()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("could not read session cache %s: %s", path, e)
        return None

    if not isinstance(data, dict):
        return None

    key = _cache_key(login_url, email)
    tokens = data.get("tokens")
    entry = None
    if isinstance(tokens, dict) and key in tokens:
        entry = tokens[key]
    elif key in data and isinstance(data[key], dict):
        entry = data[key]
    elif data.get("access_token") and (
        not data.get("email") or data.get("email").strip().lower() == email.strip().lower()
    ):
        entry = data

    if isinstance(entry, dict) and isinstance(entry.get("access_token"), str) and entry["access_token"]:
        return entry
    return None


def save_cached_session(
    session_path: Path | str,
    login_url: str,
    email: str,
    token: str,
) -> None:
    """Saves a session to disk atomically with restricted 0600 permissions."""
    path = Path(session_path).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass

        key = _cache_key(login_url, email)
        norm_url = normalise_login_url(login_url)
        clean_email = email.strip().lower()
        now = time.time()

        existing: dict = {}
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    existing = loaded
            except (OSError, ValueError):
                existing = {}

        tokens = existing.get("tokens")
        if not isinstance(tokens, dict):
            tokens = {}

        entry = {
            "access_token": token,
            "login_url": norm_url,
            "email": clean_email,
            "saved_at": now,
        }
        tokens[key] = entry

        data = {
            "access_token": token,
            "login_url": norm_url,
            "email": clean_email,
            "saved_at": now,
            "tokens": tokens,
        }

        _write_private(path, data)
    except Exception as e:
        log.warning("could not save session to %s: %s", path, e)


def clear_cached_session(session_path: Path | str, login_url: str, email: str) -> None:
    """Removes a session entry from the cache file."""
    path = Path(session_path).expanduser()
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        key = _cache_key(login_url, email)
        tokens = data.get("tokens")
        changed = False
        if isinstance(tokens, dict) and key in tokens:
            del tokens[key]
            changed = True
        if key in data:
            del data[key]
            changed = True
        if data.get("email", "").strip().lower() == email.strip().lower():
            data.pop("access_token", None)
            changed = True
        if changed:
            _write_private(path, data)
    except Exception as e:
        log.warning("could not update session cache %s: %s", path, e)

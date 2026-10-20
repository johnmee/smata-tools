"""Maintains a complete local copy of the documents a SMATA owner account can see.

SMATA (a strata-management portal) has no bulk export, documents drop out of it without warning, it can be very hard to find what you're looking for when using their web UI.

This tool logs in as the owner, lists every document and invoice, fetches each one's detail, and downloads every file it refers to: the main file, the original and PDF variants, and invoice attachments.

It also saves the raw API metadata alongside.

Nothing local is ever deleted: items that stop being listed are marked "gone" but retained locally.

Only GET requests are made against the API.

    smata-archive [config.toml]

Programmatic use:

    from smata_archive import SmataClient, Archive
    client = SmataClient(login_url, timezone="Australia/Sydney")
    client.login(email, password)
    result = Archive("smata-files").sync(client)

CLI use:

    Run this file from the commandline for CLI usage instructions:
        python <thisfilename>.py

## Architecture

The code originated from Claude AI (opus 5.5) but was fairly unintelligible to me, so I've applied a manual tidy-up to make sense of it.

* 'SmataClient' handles the Smata API client connection.
* 'DocumentsSource' talks to the Smata API /documents endpoint to list and download documents.
* 'MetadataFile' is a local json file which maps between remote and local documents and captures the remote metadata.
* 'Archive' manages the local files and data; providing synchronisation between the local and remote.

"""
from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import re
import sys
import time
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import requests

__version__ = "0.1.0"

log = logging.getLogger("smata_archive")

# Input parameters
API_BASE = "https://my.smata.com/api"
USER_AGENT = f"smata-archive/{__version__} (+https://github.com/)"
DEFAULT_CONFIG = Path("config.toml")

# The format of document filenames on local disk.  Variants and attachments extend by a
# non-numeric suffix (-original, -pdf, -att<id>) so they never match.
FILENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-(?P<category>.+)-(?P<id>\d+)\.(?P<ext>[^.]+)$")


# ---------------------------------------------------------------------------
# Errors

class SmataError(Exception):
    """Base class for every error this module raises."""


class AuthError(SmataError):
    """Login failed, or the session was rejected."""


class ApiError(SmataError):
    """An API or download request failed."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class ConfigError(SmataError):
    """Bad or missing configuration."""


# ---------------------------------------------------------------------------
# HTTP client

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
        retries: int = 5
    ):
        self.login_url = normalise_login_url(login_url)
        self.timezone = timezone
        self.api_base = api_base.rstrip("/")
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        self._api = requests.Session()
        self._api.headers.update({"User-Agent": USER_AGENT})
        self._files = requests.Session()
        self._files.headers.update({"User-Agent": USER_AGENT})
        self._token: str | None = None
        self._credentials: tuple[str, str] | None = None
        self._last_request = 0.0

    def login(self, email: str, password: str) -> None:
        """Logs in through the web form and keeps the access_token. Raises AuthError."""
        try:
            page = self._request(self._api, "GET", self.login_url, api=False)
        except ApiError as e:
            raise AuthError(f"could not load the login page {self.login_url}: {e}") from e
        match = re.search(r'name="authenticity_token" value="([^"]+)"', page.text)
        if not match:
            raise AuthError(f"no authenticity_token on {self.login_url}; is this a SMATA login URL?")
        form = {
            "utf8": "✓",
            "authenticity_token": match.group(1),
            "user[email]": email,
            "user[password]": password,
            "user[remember_me]": "0",
            "commit": "Log in",
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

    def get_json(self, path: str, **params) -> Any:
        """GETs an API path (relative to /api, e.g. "/v1/documents") and returns the JSON."""
        url = path if path.startswith("http") else f"{self.api_base}/{path.lstrip('/')}"
        try:
            resp = self._request(self._api, "GET", url, api=True, params=params)
        except AuthError:
            if not self._credentials:
                raise
            log.info("session rejected; logging in again")
            self.login(*self._credentials)
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


def normalise_login_url(url: str) -> str:
    """Clean up the login url string provided by user configuration.
    """
    url = url.strip().rstrip("/")
    if not url.startswith(("https://", "http://")):
        url = "https://" + url
    if not url.endswith("/users/sign_in"):
        url += "/users/sign_in"
    return url


def _retry_after(resp: requests.Response) -> float | None:
    try:
        return min(float(resp.headers.get("Retry-After", "")), 300)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Metadata files

class MetadataFile:
    """A json file holding every item from one source, keyed by id:
    {id: {"smata": <raw API object>, "local": {...}}}."""

    def __init__(self, path: Path):
        self.path = path
        self.items: dict[str, dict] = {}
        if path.exists():
            self.items = json.loads(path.read_text(encoding="utf-8"))

    def save(self) -> None:
        write_atomic(self.path, json.dumps(self.items, indent=1, ensure_ascii=False, sort_keys=True) + "\n")


def write_atomic(path: Path, text: str) -> None:
    """Writes text to path via path.tmp and os.replace, so a crash mid-write
    leaves either the old file or the new one, never a truncated mix."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Documents source

class DocumentsSource:
    """Documents and invoice documents: GET /v1/documents (twice) and /v1/documents/{id}."""

    name = "documents"
    folder = "documents"
    passes = ({"is_invoice": "false"}, {"is_invoice": "true"})

    def list_items(self, client: SmataClient, params: dict):
        return client.paginate("/v1/documents", "documents", time_zone=client.timezone, **params)

    def detail(self, client: SmataClient, item: dict) -> dict:
        return client.get_json(f"/v1/documents/{item['id']}")

    def needs_detail(self, entry: dict | None, item: dict) -> bool:
        """Detail is fetched for new items, changed items, and items never fetched
        successfully (a 403/404 is not retried until the item changes)."""
        if not entry or not entry.get("smata"):
            return True
        if entry["smata"].get("updated_at") != item.get("updated_at"):
            return True
        local = entry["local"]
        return not local.get("detail_fetched") and not local.get("detail_error")

    def main_filename(self, doc: dict) -> str:
        ext = doc.get("file_extension") or url_extension((doc.get("links") or {}).get("file_url") or "", "bin")
        category = safe_name(doc.get("category") or "uncategorised")
        return f"{epoch_date(doc.get('added_date') or doc.get('created_at'))}-{category}-{doc['id']}.{ext}"

    def files(self, doc: dict, main: str | None) -> list[tuple[str, str, str]]:
        """Returns (key, url, filename) for every file the document refers to.

        main is the filename if it is already on disk; variants are
        named after it so they stay together if the category is later edited."""
        links = doc.get("links") or {}
        main = main or self.main_filename(doc)
        stem = os.path.splitext(main)[0]
        out = []
        file_url = links.get("file_url")
        if file_url:
            out.append(("file", file_url, main))
        orig = links.get("original_file_url")
        if orig and orig != file_url:
            out.append(("original", orig, f"{stem}-original.{url_extension(orig, doc.get('file_extension') or 'bin')}"))
        pdf = links.get("pdf_url")
        if pdf and pdf not in (file_url, orig):
            out.append(("pdf", pdf, f"{stem}-pdf.pdf"))
        for att in ((doc.get("invoice") or {}).get("attachments") or []):
            url = att.get("file_url")
            if not url or att.get("id") is None:
                continue
            ext = att.get("file_extension") or url_extension(url, "bin")
            out.append((f"att{att['id']}", url, f"{stem}-att{att['id']}.{ext}"))
        return out


def epoch_date(ts) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts)) if ts else ""


def safe_name(text: str) -> str:
    return re.sub(r'[/\\\x00-\x1f]', "-", text).strip() or "unnamed"


def url_extension(url: str, fallback: str) -> str:
    ext = os.path.splitext(unquote(urlparse(url).path))[1].lstrip(".")
    return ext if ext and len(ext) <= 8 else fallback


# ---------------------------------------------------------------------------
# Archive


@dataclass
class SyncResult:
    new: list[str] = field(default_factory=list)        # file paths downloaded (or that would be, on a dry run)
    updated: list[str] = field(default_factory=list)    # ids whose updated_at changed
    gone: list[str] = field(default_factory=list)       # ids that stopped being listed this run
    recovered: list[str] = field(default_factory=list)  # ids rebuilt from files found on disk
    errors: list[str] = field(default_factory=list)
    counts: dict = field(default_factory=dict)


class Archive:
    """A local archive folder: files, metadata files and sync.json."""

    def __init__(self, dest: str | os.PathLike):
        self.dest = Path(dest)
        self.meta_dir = self.dest / "metadata"
        self.sources = [DocumentsSource()]
        self._metadata_files: dict[str, MetadataFile] = {}

    def metadata(self, name: str) -> dict[str, dict]:
        """The items of metadata/<name>.json, e.g. metadata("documents"), keyed by id."""
        return self._metadata_file(name).items

    def _metadata_file(self, name: str) -> MetadataFile:
        if name not in self._metadata_files:
            self._metadata_files[name] = MetadataFile(self.meta_dir / f"{name}.json")
        return self._metadata_files[name]

    # -- sync -----------------------------------------------------------------

    def sync(self, client: SmataClient, dry_run: bool = False, flush_every: int = 25) -> SyncResult:
        """Lists, fetches detail, and downloads every missing file. On a dry run,
        reports what it would download and writes nothing."""
        started = now_iso()
        result = SyncResult()
        try:
            for source in self.sources:
                self._sync_source(client, source, result, dry_run, flush_every)
        finally:
            if not dry_run:
                self._flush()
                self._record_run(started, result)
        return result

    def _sync_source(self, client, source, result: SyncResult, dry_run: bool, flush_every: int) -> None:
        meta = self._metadata_file(source.name)
        folder = self.dest / source.folder
        if not dry_run:
            for part in folder.glob("*.part"):
                part.unlink()
        on_disk = self._file_map(folder)
        now = now_iso()
        seen: set[str] = set()
        complete = True
        listed = details = downloads = 0
        pending = 0

        for params in source.passes:
            try:
                items = list(source.list_items(client, params))
            except (ApiError, AuthError) as e:
                if isinstance(e, AuthError):
                    raise
                complete = False
                msg = f"{source.name} {params}: listing failed: {e}"
                log.error(msg)
                result.errors.append(msg)
                continue
            log.info("%s %s: %d listed", source.name, params, len(items))
            for item in items:
                listed += 1
                key = str(item["id"])
                seen.add(key)
                entry = meta.items.get(key)
                if entry is None:
                    entry = {"smata": None, "local": {"files": {}, "first_seen": now}}
                    if not dry_run:
                        meta.items[key] = entry
                local = entry["local"]
                old = entry.get("smata")
                if old and old.get("updated_at") != item.get("updated_at"):
                    result.updated.append(key)

                obj = deep_merge(old, item) if old else item
                if source.needs_detail(entry, item):
                    try:
                        obj = source.detail(client, item)
                        details += 1
                        local["detail_fetched"] = now
                        local.pop("detail_error", None)
                    except ApiError as e:
                        if e.status not in (403, 404):
                            raise
                        log.warning("%s %s: detail HTTP %s; keeping list data", source.name, key, e.status)
                        local["detail_error"] = e.status
                if not dry_run:
                    entry["smata"] = obj
                    local["first_seen"] = local.get("first_seen") or now
                    local["last_seen"] = now
                    local["gone"] = False

                files = local.setdefault("files", {})
                recorded = (files.get("file") or {}).get("path")
                main = os.path.basename(recorded) if recorded else on_disk.get(key)
                for fkey, url, name in source.files(obj, main):
                    rec = files.get(fkey)
                    rel = rec["path"] if rec else f"{source.folder}/{name}"
                    target = self.dest / rel
                    if target.exists():
                        if not rec and not dry_run:
                            files[fkey] = {"path": rel, "downloaded_at": None}
                        continue
                    if dry_run:
                        result.new.append(rel)
                        continue
                    try:
                        client.download(url, target)
                    except ApiError as e:
                        msg = f"{source.name} {key} {fkey}: download failed: {e}"
                        log.error(msg)
                        result.errors.append(msg)
                        continue
                    files[fkey] = {"path": rel, "downloaded_at": now_iso(), "url": url}
                    result.new.append(rel)
                    downloads += 1
                    log.info("downloaded %s", rel)
                pending += 1
                if not dry_run and pending >= flush_every:
                    meta.save()
                    pending = 0

        # Mark gone only after every pass listed completely: a failed pass
        # would otherwise make everything it should have listed look deleted.
        if complete:
            for key, entry in meta.items.items():
                if key not in seen and not entry["local"].get("gone"):
                    result.gone.append(key)
                    if not dry_run:
                        entry["local"]["gone"] = True
                        entry["local"]["gone_since"] = now
        else:
            log.warning("%s: a listing failed, so nothing is marked gone this run", source.name)

        if not dry_run:
            for key, name in on_disk.items():
                if key not in meta.items:
                    meta.items[key] = orphan_entry(source.folder, name)
                    result.recovered.append(key)

        result.counts[source.name] = {"listed": listed, "details": details, "downloads": downloads,
                                      "known": len(meta.items), "complete": complete}

    def _file_map(self, folder: Path) -> dict[str, str]:
        """One pass over the folder: {document id: main file name}. Finding files by
        id rather than by expected name means an edited category in SMATA doesn't
        cause a duplicate download."""
        found: dict[str, str] = {}
        if folder.is_dir():
            for p in folder.iterdir():
                m = FILENAME_RE.match(p.name)
                if m and p.is_file():
                    found[m["id"]] = p.name
        return found

    def _flush(self) -> None:
        for f in self._metadata_files.values():
            f.save()

    def _record_run(self, started: str, result: SyncResult) -> None:
        path = self.meta_dir / "sync.json"
        runs = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        runs.append({"started": started, "finished": now_iso(), "version": __version__,
                     "counts": result.counts, "new": result.new, "updated": result.updated,
                     "gone": result.gone, "recovered": result.recovered, "errors": result.errors})
        write_atomic(path, json.dumps(runs, indent=1, ensure_ascii=False) + "\n")


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def orphan_entry(folder: str, name: str) -> dict:
    """A metadata entry for a file on disk that no API call ever described."""
    return {
        "smata": None,
        "local": {
            "files": {"file": {"path": f"{folder}/{name}", "downloaded_at": None}},
            "first_seen": None,
            "last_seen": None,
            "gone": True,
            "recovered": True,
        },
    }



def deep_merge(old: dict, new: dict) -> dict:
    """Merges two dicts; new wins on conflicts. Unlike {**old, **new}, nested
    dicts are merged too, so keys only old has survive at every level.

    Used to update a stored document with a fresh list response, which omits
    keys only the detail response has (history, links.original_file_url,
    invoice.attachments)."""
    out = dict(old)
    for k, v in new.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out

# ---------------------------------------------------------------------------
# Configuration and CLI


HELP_EPILOG = """\
examples:
  smata-archive                                     # uses ./config.toml
  smata-archive my-config.toml
  smata-archive --dry-run              # show what would be downloaded

config file (CONFIG), TOML:
  login_url = "https://acme.my.smata.com"
  email = "me@example.com"
  dest = "~/smata"
  timezone = "Australia/Sydney"
  password = "..."                        # optional plaintext; chmod 600 the config file; else prompted

The archive keeps every file and the raw metadata under 'dest'. Re-running
downloads only what is missing; items that disappear from SMATA are marked
"gone" in <dest>/metadata/documents.json and never deleted locally.

Exit codes: 0 ok, 1 runtime or network error, 2 usage error, 3 login failed.
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="smata-archive",
        description="Download and retain every document and invoice a SMATA owner account can see.",
        epilog=HELP_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("config", nargs="?", type=Path, default=DEFAULT_CONFIG, metavar="CONFIG",
                   help=f"TOML config file (default: {DEFAULT_CONFIG})")
    p.add_argument("-d", "--dry-run", action="store_true", help="list what would be downloaded; write nothing")
    g = p.add_mutually_exclusive_group()
    g.add_argument("-q", "--quiet", action="store_true", help="print only errors")
    g.add_argument("-v", "--verbose", action="store_true", help="print every request and file")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


@dataclass
class Config:
    login_url: str
    email: str
    dest: Path
    timezone: str
    password: str | None = None


def load_config(path: Path) -> dict:
    """Reads the TOML config. Raises ConfigError if it is missing or invalid."""
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        with open(path, "rb") as f:
            cfg = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e
    if "password" in cfg and path.stat().st_mode & 0o077:
        log.warning("%s holds a plaintext password but is readable by others; run: chmod 600 %s", path, path)
    unknown = set(cfg) - {"login_url", "email", "dest", "timezone", "password"}
    if unknown:
        raise ConfigError(f"{path}: unknown keys: {', '.join(sorted(unknown))}")
    return cfg


def resolve_config(cfg: dict) -> Config:
    missing = [k for k in ("login_url", "email", "dest", "timezone") if not cfg.get(k)]
    if missing:
        raise ConfigError(f"missing {', '.join(missing)} in the config file")
    return Config(cfg["login_url"], cfg["email"], Path(os.path.expanduser(cfg["dest"])),
                    cfg["timezone"], cfg.get("password"))


def resolve_password(config: Config) -> str:
    if config.password:
        return config.password
    if not sys.stdin.isatty():
        raise ConfigError("no password: set 'password' in the config file")
    return getpass.getpass(f"SMATA password for {config.email}: ")


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)

    level = logging.ERROR if args.quiet else logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(message)s")
    if not args.verbose:
        logging.getLogger("urllib3").setLevel(logging.WARNING)

    try:
        config = resolve_config(load_config(args.config))
        password = resolve_password(config)
    except ConfigError as e:
        print(f"smata-archive: {e}", file=sys.stderr)
        return 2

    client = SmataClient(config.login_url, timezone=config.timezone)
    try:
        client.login(config.email, password)
        result = Archive(config.dest).sync(client, dry_run=args.dry_run)
    except AuthError as e:
        print(f"smata-archive: {e}", file=sys.stderr)
        return 3
    except SmataError as e:
        print(f"smata-archive: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("smata-archive: interrupted; metadata saved, re-run to continue", file=sys.stderr)
        return 130

    verb = "would download" if args.dry_run else "new"
    if not args.quiet:
        print(f"{len(result.new)} {verb} files, {len(result.updated)} updated, "
              f"{len(result.gone)} gone, {len(result.recovered)} recovered, {len(result.errors)} errors")
        for path in result.new:
            print(f"  {path}")
    return 1 if result.errors else 0


if __name__ == "__main__":
    sys.exit(main())

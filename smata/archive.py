"""Building a local archive: listing, detail fetches, downloads and metadata.

DocumentsSource is one kind of source; Archive is the folder it writes to.  The
archive is never pruned: an item that stops being listed is marked "gone" and kept.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from smata.client import SmataClient
from smata.errors import ApiError, AuthError
from smata.version import __version__

log = logging.getLogger("smata")

# The format of document filenames on local disk.  Variants and attachments extend by a
# non-numeric suffix (-original, -pdf, -att<id>) so they never match.
FILENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-(?P<category>.+)-(?P<id>\d+)\.(?P<ext>[^.]+)$")


# ---------------------------------------------------------------------------
# Metadata files

class MetadataFile:
    """A json file holding every item from one source, keyed by id:
    {id: {"smata": <raw API object>, "local": {...}}."""

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

    def sync(self, client: SmataClient, dry_run: bool = False, flush_every: int = 25,
             version: str | None = None) -> SyncResult:
        """Lists, fetches detail, and downloads every missing file. On a dry run,
        reports what it would download and writes nothing.

        version is recorded in metadata/sync.json; it defaults to the package version.
        """
        started = now_iso()
        result = SyncResult()
        try:
            for source in self.sources:
                self._sync_source(client, source, result, dry_run, flush_every)
        finally:
            if not dry_run:
                self._flush()
                self._record_run(started, result, version or __version__)
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

    def _record_run(self, started: str, result: SyncResult, version: str) -> None:
        path = self.meta_dir / "sync.json"
        runs = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        runs.append({"started": started, "finished": now_iso(), "version": version,
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

"""Fetches one page of GET /v1/documents and stores metadata in SQLite, or lists stored documents.

Run without arguments to show the help.  The first argument is a command:

    python smata_documents.py index [config.toml] [--json]
    python smata_documents.py list [config.toml]
    python smata_documents.py download [config.toml]
    python smata_documents.py info FILE... [-c config.toml] [--json]

index loads the config (default: config.toml), fetches every page of GET /v1/documents, and stores
them in <dest>/smata.db.  If smata.db does not exist, it is created using the schema; if it already
exists, new documents are inserted and existing documents are updated.

list shows all stored documents from the local database without making network requests.

download runs index first, then saves the file of every indexed document that is not on disk into <dest>, named
<date>-<category>-<id>.<ext>, and records local_filename and downloaded_at in the database.

info prints the stored metadata of downloaded files, given as names, paths or globs; a name with no folder
is looked up in <dest>.  --json prints the raw JSON records as a list.
"""
from __future__ import annotations

import argparse
import glob
import json
import shutil
import sys
import textwrap
from datetime import datetime
from pathlib import Path

import requests

from smata.client import SmataClient
from smata.config import (
    DEFAULT_CONFIG,
    Config,
    load_config,
    resolve_config,
    resolve_password,
)
from smata.errors import AuthError, ConfigError, SmataError
from smata.naming import main_filename
from smata.storage import (
    DB_FILENAME,
    add_document,
    create_database,
    get_document,
    get_document_by_local_filename,
    list_documents,
    mark_downloaded,
    update_document,
)

#: Documents requested per GET; the API default is 50.
PER_PAGE = 200


def format_local_datetime(ts: int | None) -> str:
    """Formats a Unix epoch timestamp in seconds as local datetime YYYY-MM-DD HH:MM:SS."""
    if ts is None:
        return "-"
    return datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def fetch_documents(config: Config) -> dict:
    """Logs in and returns the JSON response of GET /v1/documents with the documents of every page."""
    client = SmataClient(config.login_url, timezone=config.timezone)
    password = config.password
    if not password and not client.has_valid_session(config.email):
        password = resolve_password(config)
    client.login(config.email, password)
    return client.get_all("/v1/documents", "documents", per_page=PER_PAGE, time_zone=client.timezone)


def save_documents(config: Config, documents: list[dict]) -> tuple[int, int, Path]:
    """Inserts or updates documents in <config.dest>/smata.db.  Returns (inserted_count, updated_count, db_path)."""
    db_path = config.dest / DB_FILENAME
    inserted = 0
    updated = 0
    conn = create_database(db_path)
    try:
        with conn:
            for doc in documents:
                stored = get_document(conn, doc["id"])
                if stored:
                    # Bumping last_seen alone is our own bookkeeping, not a change at SMATA.
                    if json.loads(stored["raw"]) != doc:
                        updated += 1
                    update_document(conn, doc)
                else:
                    add_document(conn, doc)
                    inserted += 1
    finally:
        conn.close()
    return inserted, updated, db_path


def download_files(config: Config) -> tuple[int, list[str], int]:
    """Downloads every stored document whose file is not on disk into config.dest.

    The database says what to fetch and the disk says whether it is there: a document counts as
    downloaded only if local_filename is set and that file exists, so deleted files are fetched
    again.  A file already at the target path (e.g. a run that stopped before recording it) is
    recorded without downloading it again.  Returns (downloaded, failed_ids, no_url).
    """
    db_path = config.dest / DB_FILENAME
    client = SmataClient(config.login_url, timezone=config.timezone)
    downloaded = 0
    no_url = 0
    failed = []
    conn = create_database(db_path)
    try:
        for doc in list_documents(conn):
            name = doc["local_filename"] or main_filename(json.loads(doc["raw"]))
            path = config.dest / name
            if doc["local_filename"] and path.is_file():
                continue
            if not path.is_file():
                if not doc["file_url"]:
                    no_url += 1
                    continue
                try:
                    client.download(doc["file_url"], path)
                except (SmataError, requests.RequestException, OSError) as e:
                    # The URL is a secret, so report the document id and error type only.
                    print(f"smata-documents: document {doc['id']}: download failed ({type(e).__name__})",
                          file=sys.stderr)
                    failed.append(doc["id"])
                    continue
                downloaded += 1
                print(f"Downloaded {name}", flush=True)
            mark_downloaded(conn, doc["id"], name)
    finally:
        conn.close()
    return downloaded, failed, no_url


def list_stored_documents(config: Config, out=None) -> int:
    """Lists all stored documents from <config.dest>/smata.db to out."""
    if out is None:
        out = sys.stdout
    db_path = config.dest / DB_FILENAME
    if not db_path.is_file():
        print(f"smata-documents: database not found: {db_path}", file=sys.stderr)
        return 1

    conn = create_database(db_path)
    try:
        docs = list_documents(conn)
    finally:
        conn.close()

    headers = [
        "local_filename",
        "display_name",
        "category",
        "is_invoice",
        "created_at",
        "downloaded_at",
        "first_seen",
        "last_seen",
    ]
    rows = []
    for d in docs:
        rows.append([
            d.get("local_filename") or "-",
            d.get("display_name") or "-",
            d.get("category") or "-",
            "yes" if d.get("is_invoice") else "no",
            format_local_datetime(d.get("created_at")),
            format_local_datetime(d.get("downloaded_at")),
            format_local_datetime(d.get("first_seen")),
            format_local_datetime(d.get("last_seen")),
        ])

    widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(val))

    header_line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    sep_line = "  ".join("-" * widths[i] for i in range(len(headers)))
    print(header_line, file=out)
    print(sep_line, file=out)
    for row in rows:
        print("  ".join(val.ljust(widths[i]) for i, val in enumerate(row)), file=out)
    return 0

def resolve_info_targets(config: Config, patterns: list[str]) -> list[Path]:
    """Expands filenames and globs.  A bare name (no folder) is looked up in config.dest."""
    paths: list[Path] = []
    for pattern in patterns:
        base = Path(pattern)
        if base.name == pattern:
            pattern = str(config.dest / pattern)
        matches = sorted(glob.glob(pattern))
        paths.extend(Path(m) for m in matches or [pattern])
    return paths


def format_info(doc: dict, raw: dict) -> str:
    """Human-readable metadata for one stored document."""
    lines = [f"{doc['local_filename']}"]
    bookkeeping = {
        "downloaded_at": format_local_datetime(doc["downloaded_at"]),
        "first_seen": format_local_datetime(doc["first_seen"]),
        "last_seen": format_local_datetime(doc["last_seen"]),
    }
    # The links group is flattened: file_url, document_url etc. become rows of their own.
    entries = {**bookkeeping, **{k: v for k, v in raw.items() if k != "links"}, **(raw.get("links") or {})}
    width = max(len(k) for k in entries)
    columns = shutil.get_terminal_size().columns
    for key, value in entries.items():
        if isinstance(value, int) and not isinstance(value, bool) and key.endswith(("_at", "_date")):
            value = f"{value}  ({format_local_datetime(value)})"
        elif key == "user" and isinstance(value, dict):
            value = value.get("full_name") or "-"
        elif key == "additional_users" and isinstance(value, list):
            # Wrap between names, never inside one, with the overflow kept in the value column.
            names = ", ".join(u.get("full_name", "").replace(" ", " ") for u in value) or "-"
            indent = " " * (width + 4)
            value = textwrap.fill(names, width=columns, initial_indent=indent, subsequent_indent=indent,
                                  break_long_words=False).lstrip().replace(" ", " ")
        elif isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        lines.append(f"  {key.ljust(width)}  {value}")
    return "\n".join(lines)


def run_info(config: Config, patterns: list[str], show_json: bool = False) -> int:
    """Prints the stored metadata of the named files.  Returns the exit code."""
    db_path = config.dest / DB_FILENAME
    if not db_path.is_file():
        print(f"smata-documents: database not found: {db_path}", file=sys.stderr)
        return 1

    dest = config.dest.resolve()
    found = []
    missing = 0
    conn = create_database(db_path)
    try:
        for path in resolve_info_targets(config, patterns):
            try:
                relative = str(path.resolve().relative_to(dest))
            except ValueError:
                relative = None
            doc = get_document_by_local_filename(conn, relative) if relative else None
            if doc is None:
                print(f"smata-documents: no stored document for {path}", file=sys.stderr)
                missing += 1
                continue
            found.append(doc)
    finally:
        conn.close()

    if show_json:
        json.dump([json.loads(d["raw"]) for d in found], sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        print("\n\n".join(format_info(d, json.loads(d["raw"])) for d in found))
    return 1 if missing else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch document metadata into SQLite or list stored documents.")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    config_args = argparse.ArgumentParser(add_help=False)
    config_args.add_argument("config", nargs="?", type=Path, default=DEFAULT_CONFIG, metavar="CONFIG",
                             help=f"TOML config file (default: {DEFAULT_CONFIG})")

    index = commands.add_parser("index", parents=[config_args],
                                help="fetch all documents from the API into the local database")
    index.add_argument("--json", action="store_true", help="also print the API response as JSON")

    commands.add_parser("list", parents=[config_args],
                        help="list all documents from the local database without calling the API")

    commands.add_parser("download", parents=[config_args],
                        help="download the files of indexed documents that are not on disk")

    info = commands.add_parser("info",
                               help="show the stored metadata of downloaded files (names or globs)")
    info.add_argument("files", nargs="+", metavar="FILE",
                      help="filenames or globs; a name with no folder is looked up in the destination folder")
    info.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG, metavar="CONFIG",
                      help=f"TOML config file (default: {DEFAULT_CONFIG})")
    info.add_argument("--json", action="store_true", help="print the raw JSON records")
    return parser


def run_index(config: Config, show_json: bool = False) -> int:
    """Fetches all documents into the database and reports it.  Returns the exit code."""
    try:
        response = fetch_documents(config)
        inserted, updated, db_path = save_documents(config, response.get("documents") or [])
    except ConfigError as e:
        print(f"smata-documents: {e}", file=sys.stderr)
        return 2
    except AuthError as e:
        print(f"smata-documents: {e}", file=sys.stderr)
        return 3
    except SmataError as e:
        print(f"smata-documents: {e}", file=sys.stderr)
        return 1

    listed = len(response.get("documents") or [])
    print(f"Indexed {listed} documents ({inserted} new, {updated} changed) in {db_path}")
    if show_json:
        json.dump(response, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)

    if args.command is None:
        parser.print_help()
        return 0

    try:
        config = resolve_config(load_config(args.config))
    except ConfigError as e:
        print(f"smata-documents: {e}", file=sys.stderr)
        return 2

    if args.command == "list":
        return list_stored_documents(config)

    if args.command == "info":
        return run_info(config, args.files, show_json=args.json)

    if args.command == "index":
        return run_index(config, show_json=args.json)

    # download: bring the database up to date first, and stop if that fails.
    rc = run_index(config)
    if rc:
        return rc
    downloaded, failed, no_url = download_files(config)
    print(f"Downloaded {downloaded} files into {config.dest} ({len(failed)} failed, {no_url} without a file URL)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

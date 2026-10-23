"""Fetches one page of GET /v1/documents and stores metadata in SQLite, or lists stored documents.

Run without arguments to show the help.  The first argument is a command:

    python smata_documents.py index [config.toml] [--json]
    python smata_documents.py list [config.toml]

index loads the config (default: config.toml), fetches page 1 of GET /v1/documents, and stores
them in <dest>/smata.db.  If smata.db does not exist, it is created using the schema; if it already
exists, new documents are inserted and existing documents are updated.

list shows all stored documents from the local database without making network requests.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from smata.client import SmataClient
from smata.config import (
    DEFAULT_CONFIG,
    Config,
    load_config,
    resolve_config,
    resolve_password,
)
from smata.errors import AuthError, ConfigError, SmataError
from smata.storage import (
    DB_FILENAME,
    add_document,
    create_database,
    list_documents,
    update_document,
)


def format_local_datetime(ts: int | None) -> str:
    """Formats a Unix epoch timestamp in seconds as local datetime YYYY-MM-DD HH:MM:SS."""
    if ts is None:
        return "-"
    return datetime.fromtimestamp(ts).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def fetch_documents(config: Config) -> dict:
    """Logs in and returns the JSON response for page 1 of GET /v1/documents."""
    client = SmataClient(config.login_url, timezone=config.timezone)
    password = config.password
    if not password and not client.has_valid_session(config.email):
        password = resolve_password(config)
    client.login(config.email, password)
    return client.get_json("/v1/documents", time_zone=client.timezone)


def save_documents(config: Config, documents: list[dict]) -> tuple[int, int, Path]:
    """Inserts or updates documents in <config.dest>/smata.db.  Returns (inserted_count, updated_count, db_path)."""
    db_path = config.dest / DB_FILENAME
    inserted = 0
    updated = 0
    conn = create_database(db_path)
    try:
        with conn:
            for doc in documents:
                if update_document(conn, doc):
                    updated += 1
                else:
                    add_document(conn, doc)
                    inserted += 1
    finally:
        conn.close()
    return inserted, updated, db_path


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



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch document metadata into SQLite or list stored documents.")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    config_args = argparse.ArgumentParser(add_help=False)
    config_args.add_argument("config", nargs="?", type=Path, default=DEFAULT_CONFIG, metavar="CONFIG",
                             help=f"TOML config file (default: {DEFAULT_CONFIG})")

    index = commands.add_parser("index", parents=[config_args],
                                help="fetch page 1 of documents from the API into the local database")
    index.add_argument("--json", action="store_true", help="also print the API response as JSON")

    commands.add_parser("list", parents=[config_args],
                        help="list all documents from the local database without calling the API")
    return parser


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

    total = inserted + updated
    print(f"Stored {total} documents ({inserted} new, {updated} updated) in {db_path}")
    if args.json:
        json.dump(response, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

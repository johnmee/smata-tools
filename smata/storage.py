"""SQLite storage for the documents listed by GET /v1/documents (see schema.sql).

The store keeps the raw listing record verbatim plus a few promoted columns, and
records `first_seen`/`last_seen`.  Nothing is ever deleted: a document that stops
being listed is simply absent from later listings, so its `last_seen` stops moving.

This module has no imports from the rest of the package, so it can be used on its own:

    from smata.storage import create_database

    conn = create_database("smata-files/smata.db")
    create_database(":memory:")
"""
from __future__ import annotations

import json
import sqlite3
import time
from importlib import resources
from pathlib import Path

SCHEMA_FILE = Path(__file__).with_name("schema.sql")


def schema_sql() -> str:
    """The schema DDL.  Reads from package data so an installed copy works too."""
    try:
        return resources.files("smata").joinpath("schema.sql").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, AttributeError):
        # A source tree that predates the package data entry, or a zip import.
        return SCHEMA_FILE.read_text(encoding="utf-8")


def create_database(path: str | Path) -> sqlite3.Connection:
    """Opens the SQLite database at path, creating the file and documents table if missing."""
    conn = sqlite3.connect(path)
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents'").fetchone()
    if not exists:
        conn.executescript(schema_sql())
    return conn


#: Alias for callers that prefer the verb to describe what they get back.
open_store = create_database


def _listing_fields(item: dict) -> dict:
    """The columns taken from a record of the GET /v1/documents listing."""
    return {
        "remote_filename": item["filename"],
        "file_url": (item.get("links") or {}).get("file_url"),
        "display_name": item.get("display_name"),
        "raw": json.dumps(item, ensure_ascii=False),
        "category": item.get("category"),
        "sp_number": item["sp_number"],
        "is_invoice": int(bool(item.get("is_invoice"))),
        "created_at": item.get("created_at"),
    }


def add_document(conn: sqlite3.Connection, item: dict, now: int | None = None) -> None:
    """Inserts a listing record; raises sqlite3.IntegrityError if the id is already stored."""
    now = int(time.time()) if now is None else now
    fields = _listing_fields(item) | {"id": item["id"], "first_seen": now, "last_seen": now}
    columns = ", ".join(fields)
    marks = ", ".join(f":{name}" for name in fields)
    with conn:
        conn.execute(f"INSERT INTO documents ({columns}) VALUES ({marks})", fields)


def update_document(conn: sqlite3.Connection, item: dict, now: int | None = None) -> bool:
    """Refreshes a stored document from a listing record and sets last_seen.

    Leaves local_filename, downloaded_at and first_seen alone.  Returns False if the id isn't stored.
    """
    now = int(time.time()) if now is None else now
    fields = _listing_fields(item) | {"id": item["id"], "last_seen": now}
    assignments = ", ".join(f"{name} = :{name}" for name in fields if name != "id")
    with conn:
        cursor = conn.execute(f"UPDATE documents SET {assignments} WHERE id = :id", fields)
    return cursor.rowcount > 0


def _fetch_one(conn: sqlite3.Connection, where: str, value) -> dict | None:
    cursor = conn.execute(f"SELECT * FROM documents WHERE {where} = ?", (value,))
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(zip((column[0] for column in cursor.description), row))


def get_document(conn: sqlite3.Connection, id: int) -> dict | None:
    """Returns the stored row for a document id as a dict, or None."""
    return _fetch_one(conn, "id", id)


def get_document_by_local_filename(conn: sqlite3.Connection, local_filename: str) -> dict | None:
    """Returns the stored row whose local_filename matches, or None."""
    return _fetch_one(conn, "local_filename", local_filename)


def mark_downloaded(conn: sqlite3.Connection, id: int, local_filename: str, now: int | None = None) -> bool:
    """Records that a document's file is on disk.  Returns False if the id isn't stored."""
    now = int(time.time()) if now is None else now
    with conn:
        cursor = conn.execute("UPDATE documents SET local_filename = ?, downloaded_at = ? WHERE id = ?",
                              (local_filename, now, id))
    return cursor.rowcount > 0

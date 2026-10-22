"""Tests for the SQLite document store in smata/storage.py, using in-memory databases.

The input records are in tests/sample_documents.json: 5 documents and 5 invoices chosen at random
from saved GET /v1/documents responses, with emails, person names and SMATA hosts replaced.
"""

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import smata.storage as db

SAMPLE_FILE = Path(__file__).with_name("sample_documents.json")
_sample = json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))
SAMPLE = _sample["documents"] + _sample["invoices"]


class DatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = db.create_database(":memory:")
        self.addCleanup(self.conn.close)
        self.items = copy.deepcopy(SAMPLE)

    def add_all(self, now=10):
        for item in self.items:
            db.add_document(self.conn, item, now=now)


class CreateDatabaseTests(DatabaseTestCase):
    def test_creates_file_and_table(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "docs.db"
            db.create_database(path).close()
            self.assertTrue(path.exists())
            conn = sqlite3.connect(path)
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            conn.close()
            self.assertEqual(tables, ["documents"])

    def test_reopening_keeps_rows(self):
        item = self.items[0]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "docs.db"
            conn = db.create_database(path)
            db.add_document(conn, item, now=10)
            conn.close()
            conn = db.create_database(path)
            self.assertEqual(db.get_document(conn, item["id"])["first_seen"], 10)
            conn.close()


class AddDocumentTests(DatabaseTestCase):
    def test_sample_has_documents_and_invoices(self):
        self.assertEqual(len(self.items), 10)
        self.assertEqual(len({item["id"] for item in self.items}), 10)
        self.assertEqual(sum(item["is_invoice"] for item in self.items), 5)

    def test_stores_surfaced_fields_and_raw(self):
        self.add_all(now=50)
        for item in self.items:
            with self.subTest(id=item["id"], is_invoice=item["is_invoice"]):
                row = db.get_document(self.conn, item["id"])
                self.assertEqual(row["remote_filename"], item["filename"])
                self.assertEqual(row["file_url"], item["links"]["file_url"])
                self.assertEqual(row["display_name"], item["display_name"])
                self.assertEqual(row["category"], item["category"])
                self.assertEqual(row["sp_number"], item["sp_number"])
                self.assertEqual(row["is_invoice"], int(item["is_invoice"]))
                self.assertEqual(row["created_at"], item["created_at"])
                self.assertEqual((row["first_seen"], row["last_seen"]), (50, 50))
                self.assertEqual(json.loads(row["raw"]), item)

    def test_not_downloaded_yet(self):
        self.add_all()
        for item in self.items:
            row = db.get_document(self.conn, item["id"])
            self.assertIsNone(row["local_filename"])
            self.assertIsNone(row["downloaded_at"])

    def test_missing_optional_fields_become_null(self):
        item = {"id": 2, "filename": "x.pdf", "sp_number": self.items[0]["sp_number"]}
        db.add_document(self.conn, item, now=1)
        row = db.get_document(self.conn, 2)
        self.assertIsNone(row["file_url"])
        self.assertIsNone(row["category"])
        self.assertIsNone(row["created_at"])
        self.assertEqual(row["is_invoice"], 0)

    def test_duplicate_id_raises(self):
        db.add_document(self.conn, self.items[0])
        with self.assertRaises(sqlite3.IntegrityError):
            db.add_document(self.conn, self.items[0])

    def test_defaults_now_to_current_time(self):
        db.add_document(self.conn, self.items[0])
        self.assertGreater(db.get_document(self.conn, self.items[0]["id"])["last_seen"], 1_700_000_000)


class UpdateDocumentTests(DatabaseTestCase):
    def test_refreshes_fields_and_last_seen_only(self):
        self.add_all(now=10)
        for item in self.items:
            db.mark_downloaded(self.conn, item["id"], f"{item['id']}.pdf", now=20)
        for item in self.items:
            with self.subTest(id=item["id"]):
                changed = dict(item, display_name="Renamed", category="levy notice")
                self.assertTrue(db.update_document(self.conn, changed, now=30))
                row = db.get_document(self.conn, item["id"])
                self.assertEqual(row["display_name"], "Renamed")
                self.assertEqual(row["category"], "levy notice")
                self.assertEqual(json.loads(row["raw"]), changed)
                self.assertEqual((row["first_seen"], row["last_seen"]), (10, 30))
                self.assertEqual((row["local_filename"], row["downloaded_at"]), (f"{item['id']}.pdf", 20))

    def test_unknown_id_returns_false_and_adds_nothing(self):
        item = self.items[0]
        self.assertFalse(db.update_document(self.conn, item, now=1))
        self.assertIsNone(db.get_document(self.conn, item["id"]))

    def test_only_touches_the_given_document(self):
        self.add_all(now=10)
        first, other = self.items[0], self.items[1]
        db.update_document(self.conn, dict(first, display_name="New"), now=30)
        row = db.get_document(self.conn, other["id"])
        self.assertEqual((row["display_name"], row["last_seen"]), (other["display_name"], 10))


class GetDocumentTests(DatabaseTestCase):
    def test_unknown_id_is_none(self):
        self.add_all()
        self.assertIsNone(db.get_document(self.conn, 404))

    def test_returns_dict_keyed_by_column(self):
        db.add_document(self.conn, self.items[0], now=1)
        self.assertEqual(set(db.get_document(self.conn, self.items[0]["id"])), {
            "id", "remote_filename", "file_url", "local_filename", "display_name", "raw", "category",
            "sp_number", "is_invoice", "created_at", "downloaded_at", "first_seen", "last_seen"})

    def test_by_local_filename(self):
        self.add_all()
        target = self.items[-1]
        db.mark_downloaded(self.conn, target["id"], "b.pdf", now=5)
        self.assertEqual(db.get_document_by_local_filename(self.conn, "b.pdf")["id"], target["id"])
        self.assertIsNone(db.get_document_by_local_filename(self.conn, "nope.pdf"))

    def test_by_local_filename_none_matches_nothing(self):
        self.add_all()
        self.assertIsNone(db.get_document_by_local_filename(self.conn, None))


class MarkDownloadedTests(DatabaseTestCase):
    def test_sets_filename_and_time(self):
        self.add_all(now=10)
        for item in self.items:
            with self.subTest(id=item["id"]):
                self.assertTrue(db.mark_downloaded(self.conn, item["id"], f"{item['id']}.pdf", now=40))
                row = db.get_document(self.conn, item["id"])
                self.assertEqual((row["local_filename"], row["downloaded_at"]), (f"{item['id']}.pdf", 40))
                self.assertEqual(row["last_seen"], 10)

    def test_unknown_id_returns_false(self):
        self.assertFalse(db.mark_downloaded(self.conn, 9, "a.pdf", now=1))
        self.assertIsNone(db.get_document_by_local_filename(self.conn, "a.pdf"))


if __name__ == "__main__":
    unittest.main()

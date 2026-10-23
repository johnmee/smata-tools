"""Offline tests for smata_documents.py."""

import io
import json
import sqlite3
import sys
import unittest
from unittest import mock

from smata.client import SmataClient
from smata.storage import DB_FILENAME, add_document, create_database, list_documents
from smata_documents import format_local_datetime, main
from tests.fakes import TENANT, OfflineTestCase, doc


class SmataDocumentsTests(OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.dest = self.tmp / "dest"
        self.dest.mkdir(parents=True, exist_ok=True)
        self.config_path = self.tmp / "config.toml"
        self.config_path.write_text(
            f'login_url = "{TENANT}"\n'
            f'email = "me@example.com"\n'
            f'password = "pw"\n'
            f'timezone = "UTC"\n'
            f'dest = "{self.dest}"\n'
        )

    def test_no_arguments_prints_help(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = main([])
        self.assertEqual(rc, 0)
        self.assertIn("usage:", out.getvalue())
        self.assertIn("index", out.getvalue())
        self.assertIn("list", out.getvalue())

    def test_index_creates_database_and_stores_documents(self):
        docs = [doc(1, display_name="Doc 1"), doc(2, display_name="Doc 2")]
        server, _ = self.make(docs)

        out = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = out
        try:
            with mock.patch("smata_documents.SmataClient",
                            side_effect=lambda *args, **kw: server.attach(SmataClient(*args, session_path=self.session_file, **kw))):
                rc = main(["index", str(self.config_path)])
        finally:
            sys.stdout = old_stdout

        self.assertEqual(rc, 0)
        self.assertIn("Stored 2 documents (2 new, 0 updated)", out.getvalue())

        db_path = self.dest / DB_FILENAME
        self.assertTrue(db_path.is_file())

        conn = sqlite3.connect(db_path)
        try:
            stored = list_documents(conn)
            self.assertEqual(len(stored), 2)
            self.assertEqual({d["id"] for d in stored}, {1, 2})
        finally:
            conn.close()

    def test_second_index_updates_existing_records(self):
        docs = [doc(1, display_name="Original Name")]
        server, _ = self.make(docs)

        client_patch = mock.patch("smata_documents.SmataClient",
                                  side_effect=lambda *args, **kw: server.attach(SmataClient(*args, session_path=self.session_file, **kw)))

        # First run
        with client_patch, io.StringIO() as suppressed:
            with mock.patch("sys.stdout", suppressed):
                main(["index", str(self.config_path)])

        # Update doc on fake server
        server.docs = [doc(1, display_name="Updated Name")]

        out = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = out
        try:
            with client_patch:
                rc = main(["index", str(self.config_path)])
        finally:
            sys.stdout = old_stdout

        self.assertEqual(rc, 0)
        self.assertIn("Stored 1 documents (0 new, 1 updated)", out.getvalue())

        db_path = self.dest / DB_FILENAME
        conn = sqlite3.connect(db_path)
        try:
            stored = list_documents(conn)
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0]["display_name"], "Updated Name")
        finally:
            conn.close()

    def test_list_command_prints_aligned_table_without_network_calls(self):
        db_path = self.dest / DB_FILENAME
        conn = create_database(db_path)
        try:
            add_document(conn, doc(1, display_name="Minutes AGM", category="Minutes", is_invoice=False,
                                   created_at=1700000000))
            add_document(conn, doc(2, display_name="Roof Repair", category="Invoice", is_invoice=True,
                                   created_at=1700000100))
        finally:
            conn.close()

        server, client = self.make()
        out = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = out
        try:
            rc = main(["list", str(self.config_path)])
        finally:
            sys.stdout = old_stdout

        self.assertEqual(rc, 0)
        # Server should have received 0 requests
        self.assertEqual(len(server.requests), 0)

        output = out.getvalue()
        lines = output.strip().split("\n")
        self.assertGreaterEqual(len(lines), 3)  # header, separator, and 2 rows
        self.assertIn("local_filename", lines[0])
        self.assertIn("display_name", lines[0])
        self.assertIn("is_invoice", lines[0])
        self.assertIn("created_at", lines[0])
        self.assertIn("Minutes AGM", output)
        self.assertIn("Roof Repair", output)

    def test_list_command_reports_error_if_database_missing(self):
        err = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = err
        try:
            rc = main(["list", str(self.config_path)])
        finally:
            sys.stderr = old_stderr

        self.assertEqual(rc, 1)
        self.assertIn("database not found", err.getvalue())

    def test_json_flag_outputs_raw_json(self):
        docs = [doc(42, display_name="Doc 42")]
        server, _ = self.make(docs)

        out = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = out
        try:
            with mock.patch("smata_documents.SmataClient",
                            side_effect=lambda *args, **kw: server.attach(SmataClient(*args, session_path=self.session_file, **kw))):
                rc = main(["index", str(self.config_path), "--json"])
        finally:
            sys.stdout = old_stdout

        self.assertEqual(rc, 0)
        output = out.getvalue()
        self.assertIn("Stored 1 documents", output)
        self.assertIn('"display_name": "Doc 42"', output)

    def test_format_local_datetime(self):
        self.assertEqual(format_local_datetime(None), "-")
        formatted = format_local_datetime(1700000000)
        self.assertRegex(formatted, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


if __name__ == "__main__":
    unittest.main()

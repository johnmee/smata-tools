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
from smata.naming import main_filename
from tests.fakes import FILE_HOST, TENANT, OfflineTestCase, doc


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
        self.assertIn("Indexed 2 documents (2 new, 0 changed)", out.getvalue())

        db_path = self.dest / DB_FILENAME
        self.assertTrue(db_path.is_file())

        conn = sqlite3.connect(db_path)
        try:
            stored = list_documents(conn)
            self.assertEqual(len(stored), 2)
            self.assertEqual({d["id"] for d in stored}, {1, 2})
        finally:
            conn.close()

    def test_reindexing_unchanged_documents_reports_no_updates(self):
        server, _ = self.make([doc(1)])
        client_patch = mock.patch("smata_documents.SmataClient",
                                  side_effect=lambda *args, **kw: server.attach(SmataClient(*args, session_path=self.session_file, **kw)))
        outputs = []
        for _ in range(2):
            out = io.StringIO()
            with client_patch, mock.patch("sys.stdout", out):
                main(["index", str(self.config_path)])
            outputs.append(out.getvalue())

        self.assertIn("Indexed 1 documents (1 new, 0 changed)", outputs[0])
        self.assertIn("Indexed 1 documents (0 new, 0 changed)", outputs[1])

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
        self.assertIn("Indexed 1 documents (0 new, 1 changed)", out.getvalue())

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
        self.assertIn("Indexed 1 documents", output)
        self.assertIn('"display_name": "Doc 42"', output)

    def test_format_local_datetime(self):
        self.assertEqual(format_local_datetime(None), "-")
        formatted = format_local_datetime(1700000000)
        self.assertRegex(formatted, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


class DownloadCommandTests(OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.dest = self.tmp / "dest"
        self.config_path = self.tmp / "config.toml"
        self.config_path.write_text(
            f'login_url = "{TENANT}"\nemail = "me@example.com"\npassword = "pw"\n'
            f'timezone = "UTC"\ndest = "{self.dest}"\n')
        self.docs = [doc(1), doc(2)]
        self.server, _ = self.make(self.docs)

    def run_command(self, command):
        out, err = io.StringIO(), io.StringIO()
        patch = mock.patch("smata_documents.SmataClient",
                           side_effect=lambda *a, **kw: self.server.attach(
                               SmataClient(*a, session_path=self.session_file, **kw)))
        with patch, mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            rc = main([command, str(self.config_path)])
        return rc, out.getvalue(), err.getvalue()

    def stored(self):
        conn = create_database(self.dest / DB_FILENAME)
        try:
            return {d["id"]: d for d in list_documents(conn)}
        finally:
            conn.close()

    def test_downloads_missing_files_and_updates_database(self):
        self.run_command("index")
        rc, out, _ = self.run_command("download")

        self.assertEqual(rc, 0)
        self.assertIn("Downloaded 2 files", out)
        for d in self.stored().values():
            self.assertTrue((self.dest / d["local_filename"]).is_file())
            self.assertIsNotNone(d["downloaded_at"])

    def test_second_run_downloads_nothing(self):
        self.run_command("index")
        self.run_command("download")
        before = len(self.server.file_requests())
        rc, out, _ = self.run_command("download")

        self.assertEqual(rc, 0)
        self.assertIn("Downloaded 0 files", out)
        self.assertEqual(len(self.server.file_requests()), before)

    def test_file_deleted_from_disk_is_downloaded_again(self):
        self.run_command("index")
        self.run_command("download")
        gone = self.stored()[1]["local_filename"]
        (self.dest / gone).unlink()

        rc, out, _ = self.run_command("download")

        self.assertEqual(rc, 0)
        self.assertIn("Downloaded 1 files", out)
        self.assertTrue((self.dest / gone).is_file())

    def test_existing_file_is_recorded_without_downloading(self):
        self.run_command("index")
        self.dest.mkdir(parents=True, exist_ok=True)
        name = main_filename(self.docs[0])
        (self.dest / name).write_bytes(b"already here")

        self.run_command("download")

        self.assertEqual((self.dest / name).read_bytes(), b"already here")
        self.assertEqual(self.stored()[1]["local_filename"], name)
        self.assertEqual(len(self.server.file_requests()), 1)

    def test_failed_download_is_reported_and_others_continue(self):
        self.run_command("index")
        self.server.missing_files.add(self.docs[0]["links"]["file_url"])

        rc, out, err = self.run_command("download")

        self.assertEqual(rc, 1)
        self.assertIn("1 failed", out)
        self.assertIn("document 1", err)
        self.assertNotIn(FILE_HOST, err)
        stored = self.stored()
        self.assertIsNone(stored[1]["downloaded_at"])
        self.assertIsNotNone(stored[2]["downloaded_at"])

    def test_indexes_before_downloading(self):
        rc, out, _ = self.run_command("download")

        self.assertEqual(rc, 0)
        self.assertLess(out.index("Indexed 2 documents"), out.index("Downloaded 2 files"))
        self.assertEqual(len(self.server.file_requests()), 2)

    def test_picks_up_new_documents_without_a_separate_index(self):
        self.run_command("download")
        self.server.docs.append(doc(3))

        rc, out, _ = self.run_command("download")

        self.assertEqual(rc, 0)
        self.assertIn("Downloaded 1 files", out)
        self.assertIn(3, self.stored())

    def info(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            rc = main(["info", *args, "-c", str(self.config_path)])
        return rc, out.getvalue(), err.getvalue()

    def test_info_by_bare_name_and_glob(self):
        self.run_command("download")
        name = self.stored()[1]["local_filename"]

        rc, out, _ = self.info(name)
        self.assertEqual(rc, 0)
        self.assertIn(name, out)
        self.assertIn("display_name", out)

        rc, out, _ = self.info("*.pdf")
        self.assertEqual(rc, 0)
        self.assertIn(self.stored()[2]["local_filename"], out)

    def test_info_formats_users_and_links_without_json(self):
        from smata_documents import format_info
        stored = {"local_filename": "f.pdf", "downloaded_at": None, "first_seen": 1, "last_seen": 1}
        raw = {"additional_users": [{"id": 7, "full_name": "Ann Lee"}, {"id": 8, "full_name": "Bo Kim"}],
               "links": {"file_url": "https://f/x", "thumb_url": "https://f/t"}}
        text = format_info(stored, raw)
        self.assertIn("Ann Lee, Bo Kim\n", text)
        self.assertNotIn("7", text.split("additional_users")[1].split("links")[0])
        self.assertIn("file_url: https://f/x\n", text)
        self.assertIn("thumb_url: https://f/t", text)
        self.assertNotIn("{", text)

    def test_info_by_path(self):
        self.run_command("download")
        name = self.stored()[1]["local_filename"]
        rc, out, _ = self.info(str(self.dest / name))
        self.assertEqual(rc, 0)
        self.assertIn(name, out)

    def test_info_json_is_the_raw_records(self):
        self.run_command("download")
        rc, out, _ = self.info("*.pdf", "--json")
        self.assertEqual(rc, 0)
        self.assertEqual({r["id"] for r in json.loads(out)}, {1, 2})

    def test_info_unknown_file_is_an_error(self):
        self.run_command("download")
        rc, out, err = self.info("nope.pdf")
        self.assertEqual(rc, 1)
        self.assertIn("nope.pdf", err)

    def test_failed_index_stops_before_downloading(self):
        self.server.password = "changed"

        rc, out, err = self.run_command("download")

        self.assertEqual(rc, 3)
        self.assertNotIn("Downloaded", out)
        self.assertEqual(self.server.file_requests(), [])


if __name__ == "__main__":
    unittest.main()

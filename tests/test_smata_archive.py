"""Offline tests for Archive and DocumentsSource. Run with `python -m unittest` or `pytest`.

Sync tests run a real SmataClient and Archive against FakeSmata (tests/fakes.py), so they cover
login, pagination and downloads as well as the archive logic.  All data is fake.
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import smata_archive as sa
from tests.fakes import T0, OfflineTestCase, doc

MINUTES_1 = "documents/2023-11-14-Minutes-1.pdf"
MINUTES_2 = "documents/2023-11-14-Minutes-2.pdf"


class FilenameTests(unittest.TestCase):
    def test_main_filename(self):
        src = sa.DocumentsSource()
        self.assertEqual(src.main_filename(doc(42, category="Levy/Notice")), "2023-11-14-Levy-Notice-42.pdf")
        self.assertEqual(src.main_filename(doc(42, category=None)), "2023-11-14-uncategorised-42.pdf")

    def test_variant_and_attachment_names_never_parse_as_documents(self):
        d = doc(42, links={"file_url": "u1", "original_file_url": "https://x/o.docx", "pdf_url": "https://x/p.pdf"},
                invoice={"attachments": [{"id": 777, "file_url": "https://x/a.jpg", "file_extension": "jpg"}]})
        files = sa.DocumentsSource().files(d, None)
        names = {k: n for k, _, n in files}
        self.assertEqual(names, {"file": "2023-11-14-Minutes-42.pdf", "original": "2023-11-14-Minutes-42-original.docx",
                                 "pdf": "2023-11-14-Minutes-42-pdf.pdf", "att777": "2023-11-14-Minutes-42-att777.jpg"})
        for k, n in names.items():
            if k != "file":
                self.assertIsNone(sa.FILENAME_RE.match(n), n)

    def test_variants_follow_existing_main_name(self):
        d = doc(42, category="Renamed", links={"file_url": "u", "pdf_url": "p"})
        names = [n for _, _, n in sa.DocumentsSource().files(d, "2020-01-01-Old-42.pdf")]
        self.assertEqual(names, ["2020-01-01-Old-42.pdf", "2020-01-01-Old-42-pdf.pdf"])


class SyncTests(OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.dest = self.tmp

    def start(self, docs, **client_kw):
        """A logged-in client talking to a FakeSmata holding docs.  Returns (server, client)."""
        self.server, self.client = self.logged_in(docs, **client_kw)
        return self.server, self.client

    def sync(self, **kw):
        return sa.Archive(self.dest).sync(self.client, **kw)

    def metadata(self):
        return json.loads((self.dest / "metadata" / "documents.json").read_text())

    def test_downloads_documents_invoices_variants_and_attachments(self):
        server, _ = self.start([doc(1), doc(2, category="Invoice", is_invoice=True)])
        server.details = {
            1: {"links": {"original_file_url": "https://files.example/1/o.doc"}, "history": [{"name": "x"}]},
            2: {"invoice": {"attachments": [{"id": 9, "file_url": "https://files.example/9/a.pdf"}]}}}
        r = self.sync()
        self.assertEqual(sorted(r.new), ["documents/2023-11-14-Invoice-2-att9.pdf", "documents/2023-11-14-Invoice-2.pdf",
                                         "documents/2023-11-14-Minutes-1-original.doc", MINUTES_1])
        s = self.metadata()
        self.assertEqual(s["1"]["smata"]["history"], [{"name": "x"}])
        self.assertEqual(s["1"]["local"]["files"]["original"]["url"], "https://files.example/1/o.doc")
        runs = json.loads((self.dest / "metadata" / "sync.json").read_text())
        self.assertEqual(len(runs[0]["new"]), 4)

    def test_file_content_comes_from_the_file_host(self):
        server, _ = self.start([doc(1)])
        self.sync()
        self.assertEqual((self.dest / MINUTES_1).read_bytes(), server.docs[0]["links"]["file_url"].encode())

    def test_rerun_downloads_nothing_and_skips_unchanged_detail(self):
        server, _ = self.start([doc(1)])
        self.sync()
        files = server.file_requests()
        r = self.sync()
        self.assertEqual(r.new, [])
        self.assertEqual(server.detail_requests(), [1])
        self.assertEqual(server.file_requests(), files)

    def test_list_merge_keeps_detail_only_fields(self):
        server, _ = self.start([doc(1)])
        server.details = {1: {"links": {"pdf_url": "https://x/p.pdf"}, "history": [1], "new_field": 5}}
        self.sync()
        server.docs[0] = doc(1, display_name="Edited")    # updated_at unchanged, so detail is not refetched
        self.sync()
        smata = self.metadata()["1"]["smata"]
        self.assertEqual(smata["display_name"], "Edited")
        self.assertEqual(smata["links"]["pdf_url"], "https://x/p.pdf")
        self.assertEqual(smata["new_field"], 5)
        self.assertEqual(server.detail_requests(), [1])

    def test_changed_updated_at_refetches_detail(self):
        server, _ = self.start([doc(1)])
        self.sync()
        server.docs[0] = doc(1, updated_at=T0 + 1)
        r = self.sync()
        self.assertEqual(r.updated, ["1"])
        self.assertEqual(server.detail_requests(), [1, 1])

    def test_deleted_file_is_redownloaded(self):
        self.start([doc(1), doc(2)])
        self.sync()
        (self.dest / MINUTES_1).unlink()
        r = self.sync()
        self.assertEqual(r.new, [MINUTES_1])

    def test_category_edit_does_not_duplicate(self):
        server, _ = self.start([doc(1)])
        self.sync()
        server.docs[0] = doc(1, category="Other", updated_at=T0 + 1)
        r = self.sync()
        self.assertEqual(r.new, [])
        self.assertEqual(os.listdir(self.dest / "documents"), ["2023-11-14-Minutes-1.pdf"])

    def test_gone_marked_and_files_kept(self):
        server, _ = self.start([doc(1), doc(2)])
        self.sync()
        server.docs[:] = [doc(1)]
        r = self.sync()
        self.assertEqual(r.gone, ["2"])
        self.assertTrue(self.metadata()["2"]["local"]["gone"])
        self.assertTrue((self.dest / MINUTES_2).exists())
        server.docs[:] = [doc(1), doc(2)]
        self.sync()
        self.assertFalse(self.metadata()["2"]["local"]["gone"])

    def test_failed_pass_marks_nothing_gone(self):
        server, _ = self.start([doc(1), doc(2, is_invoice=True)], retries=0)
        self.sync()
        server.failing_lists.add("true")
        r = self.sync()
        self.assertEqual(r.gone, [])
        self.assertFalse(self.metadata()["2"]["local"]["gone"])
        self.assertEqual(len(r.errors), 1)

    def test_detail_404_keeps_list_data_and_is_not_retried(self):
        server, _ = self.start([doc(1)])
        server.missing_detail.add(1)
        r = self.sync()
        self.assertEqual(r.new, [MINUTES_1])
        self.assertEqual(r.errors, [])
        self.assertEqual(self.metadata()["1"]["local"]["detail_error"], 404)
        self.sync()
        self.assertEqual(server.detail_requests(), [1])

    def test_missing_file_is_reported_and_retried_next_run(self):
        server, _ = self.start([doc(1)])
        server.missing_files.add(server.docs[0]["links"]["file_url"])
        r = self.sync()
        self.assertEqual((r.new, len(r.errors)), ([], 1))
        server.missing_files.clear()
        self.assertEqual(self.sync().new, [MINUTES_1])

    def test_dry_run_writes_nothing(self):
        server, _ = self.start([doc(1)])
        r = self.sync(dry_run=True)
        self.assertEqual(r.new, [MINUTES_1])
        self.assertEqual(list(self.dest.iterdir()), [])
        self.assertEqual(server.file_requests(), [])

    def test_part_files_are_discarded(self):
        self.start([doc(1)])
        (self.dest / "documents").mkdir()
        (self.dest / "documents" / "2023-11-14-Minutes-1.pdf.part").write_text("half")
        r = self.sync()
        self.assertEqual(r.new, [MINUTES_1])
        self.assertEqual(os.listdir(self.dest / "documents"), ["2023-11-14-Minutes-1.pdf"])

    def test_interrupt_saves_metadata_and_resumes(self):
        _, client = self.start([doc(1), doc(2)])
        real = client.download

        def interrupt_on_second(url, path):
            if "/2/" in url:
                raise KeyboardInterrupt
            return real(url, path)

        with mock.patch.object(client, "download", side_effect=interrupt_on_second), \
                self.assertRaises(KeyboardInterrupt):
            self.sync()
        self.assertIn("1", self.metadata())
        r = self.sync()
        self.assertEqual(r.new, [MINUTES_2])

    def test_orphan_file_recovered(self):
        self.start([])
        (self.dest / "documents").mkdir()
        (self.dest / "documents" / "2020-01-01-Fire-55.pdf").write_text("x")
        (self.dest / "documents" / "2020-01-01-Fire-55-att66.pdf").write_text("x")
        r = self.sync()
        self.assertEqual(r.recovered, ["55"])
        self.assertNotIn("66", self.metadata())


if __name__ == "__main__":
    unittest.main()

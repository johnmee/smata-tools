"""Offline tests. Run with `python -m unittest` or `pytest`. All data is fake."""

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import smata_archive as sa  # noqa: E402

T0 = 1_700_000_000  # 2023-11-14


def doc(id, category="Minutes", is_invoice=False, updated_at=T0, **extra):
    d = {"id": id, "category": category, "is_invoice": is_invoice, "file_extension": "pdf",
         "added_date": T0, "created_at": T0, "updated_at": updated_at, "display_name": f"Doc {id}",
         "links": {"file_url": f"https://files.example/{id}/f.pdf"}}
    d.update(extra)
    return d


class FakeClient:
    """Stands in for SmataClient at the get_json/paginate/download seam."""

    timezone = "UTC"

    def __init__(self, docs, invoices=(), details=None, fail_pass=None, missing_detail=()):
        self.lists = {"false": list(docs), "true": list(invoices)}
        self.details = details or {}
        self.fail_pass = fail_pass
        self.missing_detail = set(missing_detail)
        self.detail_calls = []
        self.downloads = []

    def paginate(self, path, key, **params):
        assert path == "/v1/documents"
        if params["is_invoice"] == self.fail_pass:
            raise sa.ApiError("boom", 500)
        return iter(self.lists[params["is_invoice"]])

    def get_json(self, path, **params):
        id = int(path.rsplit("/", 1)[1])
        self.detail_calls.append(id)
        if id in self.missing_detail:
            raise sa.ApiError("Attachment Not Found", 404)
        for d in self.lists["false"] + self.lists["true"]:
            if d["id"] == id:
                return sa.deep_merge(d, self.details.get(id, {}))
        raise sa.ApiError("nope", 404)

    def download(self, url, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(url.encode())
        self.downloads.append(url)
        return len(url)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dest = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def sync(self, client, **kw):
        return sa.Archive(self.dest).sync(client, **kw)

    def metadata(self):
        return json.loads((self.dest / "metadata" / "documents.json").read_text())


class FilenameTests(Base):
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


class SyncTests(Base):
    def test_downloads_documents_invoices_variants_and_attachments(self):
        inv = doc(2, category="Invoice", is_invoice=True)
        client = FakeClient([doc(1)], [inv], details={
            1: {"links": {"original_file_url": "https://files.example/1/o.doc"}, "history": [{"name": "x"}]},
            2: {"invoice": {"attachments": [{"id": 9, "file_url": "https://files.example/9/a.pdf"}]}}})
        r = self.sync(client)
        self.assertEqual(sorted(r.new), ["documents/2023-11-14-Invoice-2-att9.pdf", "documents/2023-11-14-Invoice-2.pdf",
                                         "documents/2023-11-14-Minutes-1-original.doc", "documents/2023-11-14-Minutes-1.pdf"])
        s = self.metadata()
        self.assertEqual(s["1"]["smata"]["history"], [{"name": "x"}])
        self.assertEqual(s["1"]["local"]["files"]["original"]["url"], "https://files.example/1/o.doc")
        runs = json.loads((self.dest / "metadata" / "sync.json").read_text())
        self.assertEqual(len(runs[0]["new"]), 4)

    def test_rerun_downloads_nothing_and_skips_unchanged_detail(self):
        client = FakeClient([doc(1)])
        self.sync(client)
        client.detail_calls.clear()
        r = self.sync(client)
        self.assertEqual(r.new, [])
        self.assertEqual(client.detail_calls, [])

    def test_list_merge_keeps_detail_only_fields(self):
        client = FakeClient([doc(1)], details={1: {"links": {"pdf_url": "https://x/p.pdf"}, "history": [1], "new_field": 5}})
        self.sync(client)
        self.sync(FakeClient([doc(1, display_name="Edited")]))
        smata = self.metadata()["1"]["smata"]
        self.assertEqual(smata["display_name"], "Edited")
        self.assertEqual(smata["links"]["pdf_url"], "https://x/p.pdf")
        self.assertEqual(smata["new_field"], 5)

    def test_changed_updated_at_refetches_detail(self):
        self.sync(FakeClient([doc(1)]))
        client = FakeClient([doc(1, updated_at=T0 + 1)])
        r = self.sync(client)
        self.assertEqual(r.updated, ["1"])
        self.assertEqual(client.detail_calls, [1])

    def test_deleted_file_is_redownloaded(self):
        self.sync(FakeClient([doc(1), doc(2)]))
        (self.dest / "documents" / "2023-11-14-Minutes-1.pdf").unlink()
        r = self.sync(FakeClient([doc(1), doc(2)]))
        self.assertEqual(r.new, ["documents/2023-11-14-Minutes-1.pdf"])

    def test_category_edit_does_not_duplicate(self):
        self.sync(FakeClient([doc(1)]))
        r = self.sync(FakeClient([doc(1, category="Other", updated_at=T0 + 1)]))
        self.assertEqual(r.new, [])
        self.assertEqual(os.listdir(self.dest / "documents"), ["2023-11-14-Minutes-1.pdf"])

    def test_gone_marked_and_files_kept(self):
        self.sync(FakeClient([doc(1), doc(2)]))
        r = self.sync(FakeClient([doc(1)]))
        self.assertEqual(r.gone, ["2"])
        s = self.metadata()
        self.assertTrue(s["2"]["local"]["gone"])
        self.assertTrue((self.dest / "documents" / "2023-11-14-Minutes-2.pdf").exists())
        r = self.sync(FakeClient([doc(1), doc(2)]))
        self.assertFalse(self.metadata()["2"]["local"]["gone"])

    def test_failed_pass_marks_nothing_gone(self):
        self.sync(FakeClient([doc(1)], [doc(2, is_invoice=True)]))
        r = self.sync(FakeClient([doc(1)], [], fail_pass="true"))
        self.assertEqual(r.gone, [])
        self.assertFalse(self.metadata()["2"]["local"]["gone"])
        self.assertEqual(len(r.errors), 1)

    def test_detail_404_keeps_list_data_and_is_not_retried(self):
        client = FakeClient([doc(1)], missing_detail=[1])
        r = self.sync(client)
        self.assertEqual(r.new, ["documents/2023-11-14-Minutes-1.pdf"])
        self.assertEqual(self.metadata()["1"]["local"]["detail_error"], 404)
        client.detail_calls.clear()
        self.sync(client)
        self.assertEqual(client.detail_calls, [])

    def test_dry_run_writes_nothing(self):
        r = self.sync(FakeClient([doc(1)]), dry_run=True)
        self.assertEqual(r.new, ["documents/2023-11-14-Minutes-1.pdf"])
        self.assertEqual(list(self.dest.iterdir()), [])

    def test_part_files_are_discarded(self):
        (self.dest / "documents").mkdir()
        (self.dest / "documents" / "2023-11-14-Minutes-1.pdf.part").write_text("half")
        r = self.sync(FakeClient([doc(1)]))
        self.assertEqual(r.new, ["documents/2023-11-14-Minutes-1.pdf"])
        self.assertEqual(os.listdir(self.dest / "documents"), ["2023-11-14-Minutes-1.pdf"])

    def test_interrupt_saves_metadata_and_resumes(self):
        client = FakeClient([doc(1), doc(2)])
        real = client.download

        def interrupt_on_second(url, path):
            if "/2/" in url:
                raise KeyboardInterrupt
            return real(url, path)

        client.download = interrupt_on_second
        with self.assertRaises(KeyboardInterrupt):
            self.sync(client)
        self.assertIn("1", self.metadata())
        r = self.sync(FakeClient([doc(1), doc(2)]))
        self.assertEqual(r.new, ["documents/2023-11-14-Minutes-2.pdf"])

    def test_orphan_file_recovered(self):
        (self.dest / "documents").mkdir()
        (self.dest / "documents" / "2020-01-01-Fire-55.pdf").write_text("x")
        (self.dest / "documents" / "2020-01-01-Fire-55-att66.pdf").write_text("x")
        r = self.sync(FakeClient([]))
        self.assertEqual(r.recovered, ["55"])
        self.assertNotIn("66", self.metadata())


class ConfigTests(Base):
    CFG = {"login_url": "cfg.example", "email": "cfg@x", "dest": "~/cfgdest", "timezone": "UTC"}

    def test_config_values(self):
        s = sa.resolve_config(self.CFG)
        self.assertEqual((s.login_url, s.email, s.dest, s.timezone),
                         ("cfg.example", "cfg@x", Path(os.path.expanduser("~/cfgdest")), "UTC"))

    def test_every_required_key_is_enforced(self):
        for key in ("login_url", "email", "dest", "timezone"):
            cfg = {k: v for k, v in self.CFG.items() if k != key}
            with self.assertRaisesRegex(sa.ConfigError, key):
                sa.resolve_config(cfg)

    def test_missing_config_file_is_a_config_error(self):
        with self.assertRaisesRegex(sa.ConfigError, "not found"):
            sa.load_config(self.dest / "nope.toml")

    def test_password_in_config_used(self):
        p = self.dest / "c.toml"
        p.write_text('login_url = "x"\nemail = "e"\ndest = "d"\ntimezone = "UTC"\npassword = "secret"\n')
        p.chmod(0o600)
        s = sa.resolve_config(sa.load_config(p))
        self.assertEqual(sa.resolve_password(s), "secret")

    def test_login_url_normalised(self):
        for u in ("acme.my.smata.com", "https://acme.my.smata.com/", "https://acme.my.smata.com/users/sign_in"):
            self.assertEqual(sa.normalise_login_url(u), "https://acme.my.smata.com/users/sign_in")


class CliTests(unittest.TestCase):
    def test_help_flag_prints_help(self):
        out = io.StringIO()
        old, sys.stdout = sys.stdout, out
        try:
            self.assertEqual(sa.main(["--help"]), 0)
        finally:
            sys.stdout = old
        self.assertIn("Exit codes", out.getvalue())

    def test_no_args_uses_config_toml_in_cwd(self):
        old_cwd, old_err = os.getcwd(), sys.stderr
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            sys.stderr = io.StringIO()
            try:
                self.assertEqual(sa.main([]), 2)
                self.assertIn("config.toml", sys.stderr.getvalue())
            finally:
                os.chdir(old_cwd)
                sys.stderr = old_err

    def test_bad_flag_is_usage_error(self):
        old, sys.stderr = sys.stderr, io.StringIO()
        try:
            self.assertEqual(sa.main(["--nope"]), 2)
            self.assertEqual(sa.main(["--email", "a@b"]), 2)
        finally:
            sys.stderr = old

    def test_missing_config_file_is_usage_error(self):
        old, sys.stderr = sys.stderr, io.StringIO()
        try:
            self.assertEqual(sa.main(["/nonexistent/config.toml"]), 2)
        finally:
            sys.stderr = old


class ClientTests(unittest.TestCase):
    def test_bearer_token_not_sent_to_file_host(self):
        c = sa.SmataClient("acme.my.smata.com", "UTC", min_interval=0)
        c._token = "secret"
        seen = {}

        class Resp:
            status_code = 200
            headers = {}
            def iter_content(self, chunk_size): yield b"data"
            def close(self): pass

        def fake_request(method, url, headers=None, **kw):
            seen[url] = headers
            return Resp()

        c._files.request = fake_request
        c._api.request = fake_request
        with tempfile.TemporaryDirectory() as d:
            c.download("https://s3.example/f.pdf", Path(d) / "f.pdf")
            self.assertEqual((Path(d) / "f.pdf").read_bytes(), b"data")
        self.assertNotIn("Authorization", seen["https://s3.example/f.pdf"])
        self.assertNotIn("Authorization", c._files.headers)

    def test_accept_json_only_on_api_calls(self):
        c = sa.SmataClient("acme.my.smata.com", "UTC", min_interval=0)
        c._token = "secret"
        sent = []

        class Resp:
            status_code = 200
            headers = {}
            text = '<input name="authenticity_token" value="tok">'
            def json(self): return {}
            def close(self): pass

        def fake_request(method, url, headers=None, **kw):
            sent.append((method, url, dict(headers or {})))
            return Resp()

        c._api.request = fake_request
        self.assertNotEqual(c._api.headers.get("Accept"), "application/json")
        c._request(c._api, "GET", c.login_url, api=False)
        c._request(c._api, "POST", c.login_url, api=False, data={})
        c.get_json("/v1/documents")
        for method, url, headers in sent[:2]:
            self.assertNotIn("Accept", headers, f"{method} {url}")
        self.assertEqual(sent[2][2]["Accept"], "application/json")
        self.assertEqual(sent[2][2]["Authorization"], "Bearer secret")


if __name__ == "__main__":
    unittest.main()

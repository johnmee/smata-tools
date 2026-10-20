"""Offline tests for the config file and the command line."""

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import smata_archive as sa


class ConfigTests(unittest.TestCase):
    CFG: ClassVar[dict[str, str]] = {
        "login_url": "cfg.example",
        "email": "cfg@x",
        "dest": "~/cfgdest",
        "timezone": "UTC"
    }

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dest = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

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


if __name__ == "__main__":
    unittest.main()

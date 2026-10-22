"""Offline tests for the command line and the config file."""

import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

from smata.cli import main
from smata.config import load_config, resolve_config, resolve_password
from smata.errors import ConfigError


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
        s = resolve_config(self.CFG)
        self.assertEqual((s.login_url, s.email, s.dest, s.timezone),
                         ("cfg.example", "cfg@x", Path(os.path.expanduser("~/cfgdest")), "UTC"))

    def test_every_required_key_is_enforced(self):
        for key in ("login_url", "email", "dest", "timezone"):
            cfg = {k: v for k, v in self.CFG.items() if k != key}
            with self.assertRaisesRegex(ConfigError, key):
                resolve_config(cfg)

    def test_missing_config_file_is_a_config_error(self):
        with self.assertRaisesRegex(ConfigError, "not found"):
            load_config(self.dest / "nope.toml")

    def test_config_path_may_be_a_string(self):
        p = self.dest / "c.toml"
        p.write_text('login_url = "x"\nemail = "e"\ndest = "d"\ntimezone = "UTC"\npassword = "secret"\n')
        p.chmod(0o600)
        s = resolve_config(load_config(str(p)))
        self.assertEqual(resolve_password(s), "secret")

    def test_unknown_key_is_a_config_error(self):
        p = self.dest / "c.toml"
        p.write_text('login_url = "x"\nemail = "e"\ndest = "d"\ntimezone = "UTC"\nnot_a_key = 1\n')
        p.chmod(0o600)
        with self.assertRaisesRegex(ConfigError, "not_a_key"):
            load_config(p)

    def test_every_documented_key_is_accepted(self):
        """config.example.toml must be a valid config, or the example lies."""
        example = (Path(__file__).resolve().parent.parent / "config.example.toml").read_text(encoding="utf-8")
        import tomllib

        raw = tomllib.loads(example)
        raw.update({"dest": "/tmp/x", "email": "e@x", "login_url": "https://x", "timezone": "UTC",
                    "password": "pw"})
        self.assertEqual(resolve_config(raw).email, "e@x")

    def test_password_in_config_used(self):
        p = self.dest / "c.toml"
        p.write_text('login_url = "x"\nemail = "e"\ndest = "d"\ntimezone = "UTC"\npassword = "secret"\n')
        p.chmod(0o600)
        s = resolve_config(load_config(p))
        self.assertEqual(resolve_password(s), "secret")


class CliTests(unittest.TestCase):
    def test_help_flag_prints_help(self):
        out = io.StringIO()
        old, sys.stdout = sys.stdout, out
        try:
            self.assertEqual(main(["--help"]), 0)
        finally:
            sys.stdout = old
        self.assertIn("Exit codes", out.getvalue())

    def test_no_args_uses_config_toml_in_cwd(self):
        old_cwd, old_err = os.getcwd(), sys.stderr
        with tempfile.TemporaryDirectory() as d:
            os.chdir(d)
            sys.stderr = io.StringIO()
            try:
                self.assertEqual(main([]), 2)
                self.assertIn("config.toml", sys.stderr.getvalue())
            finally:
                os.chdir(old_cwd)
                sys.stderr = old_err

    def test_bad_flag_is_usage_error(self):
        old, sys.stderr = sys.stderr, io.StringIO()
        try:
            self.assertEqual(main(["--nope"]), 2)
            self.assertEqual(main(["--email", "a@b"]), 2)
        finally:
            sys.stderr = old

    def test_missing_config_file_is_usage_error(self):
        old, sys.stderr = sys.stderr, io.StringIO()
        try:
            self.assertEqual(main(["/nonexistent/config.toml"]), 2)
        finally:
            sys.stderr = old


if __name__ == "__main__":
    unittest.main()

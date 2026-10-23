"""Offline tests for the config file."""

import os
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

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


if __name__ == "__main__":
    unittest.main()

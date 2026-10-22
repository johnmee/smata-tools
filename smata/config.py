"""Reading and validating the TOML config file.

The config file is the only source of settings: no environment variables and no
command line flags for them.  See config.example.toml.
"""
from __future__ import annotations

import getpass
import logging
import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from smata.errors import ConfigError

log = logging.getLogger("smata")

DEFAULT_CONFIG = Path("config.toml")

#: Every key a config file may hold.  An unknown key is an error, so a typo in the
#: file fails loudly instead of being silently ignored.
KNOWN_KEYS = ("login_url", "email", "dest", "timezone", "password")


@dataclass
class Config:
    """The settings a run needs, with `dest` already expanded and turned into a Path."""

    login_url: str
    email: str
    dest: Path
    timezone: str
    password: str | None = None


def load_config(path: str | Path) -> dict:
    """Reads the TOML config. Raises ConfigError if it is missing or invalid."""
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        with open(path, "rb") as f:
            cfg = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e
    if "password" in cfg and path.stat().st_mode & 0o077:
        log.warning("%s holds a plaintext password but is readable by others; run: chmod 600 %s", path, path)
    unknown = set(cfg) - set(KNOWN_KEYS)
    if unknown:
        raise ConfigError(f"{path}: unknown keys: {', '.join(sorted(unknown))}")
    return cfg


def resolve_config(cfg: dict) -> Config:
    """Checks a raw config dict and returns a Config. Raises ConfigError if a key is missing."""
    missing = [k for k in KNOWN_KEYS if k != "password" and not cfg.get(k)]
    if missing:
        raise ConfigError(f"missing {', '.join(missing)} in the config file")
    return Config(cfg["login_url"], cfg["email"], Path(os.path.expanduser(cfg["dest"])),
                  cfg["timezone"], cfg.get("password"))


def resolve_password(config: Config) -> str:
    """The password from the config file, or an interactive prompt. Raises ConfigError."""
    if config.password:
        return config.password
    if not sys.stdin.isatty():
        raise ConfigError("no password: set 'password' in the config file")
    return getpass.getpass(f"SMATA password for {config.email}: ")

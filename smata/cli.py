"""Argument parsing and exit codes for the smata-archive command.

Only this module touches argparse and stdout.  It does no work of its own: it loads
the config, logs in, and hands over to smata.archive.Archive.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from smata.archive import Archive
from smata.client import SmataClient
from smata.config import DEFAULT_CONFIG, load_config, resolve_config, resolve_password
from smata.errors import AuthError, ConfigError, SmataError
from smata.version import __version__

log = logging.getLogger("smata")

HELP_EPILOG = """\
examples:
  smata-archive                                     # uses ./config.toml
  smata-archive my-config.toml
  smata-archive --dry-run              # show what would be downloaded

config file (CONFIG), TOML:
  login_url = "https://acme.my.smata.com"
  email = "me@example.com"
  dest = "~/smata"
  timezone = "Australia/Sydney"
  password = "..."                        # optional plaintext; chmod 600 the config file; else prompted

The archive keeps every file and the raw metadata under 'dest'. Re-running
downloads only what is missing; items that disappear from SMATA are marked
"gone" in <dest>/metadata/documents.json and never deleted locally.

Exit codes: 0 ok, 1 runtime or network error, 2 usage error, 3 login failed.
"""


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="smata-archive",
        description="Download and retain every document and invoice a SMATA owner account can see.",
        epilog=HELP_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("config", nargs="?", type=Path, default=DEFAULT_CONFIG, metavar="CONFIG",
                   help=f"TOML config file (default: {DEFAULT_CONFIG})")
    p.add_argument("-d", "--dry-run", action="store_true", help="list what would be downloaded; write nothing")
    g = p.add_mutually_exclusive_group()
    g.add_argument("-q", "--quiet", action="store_true", help="print only errors")
    g.add_argument("-v", "--verbose", action="store_true", help="print every request and file")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)

    level = logging.ERROR if args.quiet else logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(message)s")
    if not args.verbose:
        logging.getLogger("urllib3").setLevel(logging.WARNING)

    try:
        config = resolve_config(load_config(args.config))
        password = resolve_password(config)
    except ConfigError as e:
        print(f"smata-archive: {e}", file=sys.stderr)
        return 2

    client = SmataClient(config.login_url, timezone=config.timezone)
    try:
        client.login(config.email, password)
        result = Archive(config.dest).sync(client, dry_run=args.dry_run)
    except AuthError as e:
        print(f"smata-archive: {e}", file=sys.stderr)
        return 3
    except SmataError as e:
        print(f"smata-archive: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("smata-archive: interrupted; metadata saved, re-run to continue", file=sys.stderr)
        return 130

    verb = "would download" if args.dry_run else "new"
    if not args.quiet:
        print(f"{len(result.new)} {verb} files, {len(result.updated)} updated, "
              f"{len(result.gone)} gone, {len(result.recovered)} recovered, {len(result.errors)} errors")
        for path in result.new:
            print(f"  {path}")
    return 1 if result.errors else 0

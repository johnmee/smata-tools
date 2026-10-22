"""Fetches one page of GET /v1/documents and prints the response to stdout.

Logs in with the credentials in the config file (see config.example.toml), makes a single
read-only request, and writes the JSON response to stdout.  Errors go to stderr.

    python smata_documents.py [config.toml]

The request has no filters, so the API applies its defaults: the first page of the
documents that are not invoices (is_invoice=false).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from smata.client import SmataClient
from smata.config import DEFAULT_CONFIG, load_config, resolve_config, resolve_password
from smata.errors import AuthError, ConfigError, SmataError


def fetch_documents(config_path: Path) -> dict:
    """Logs in using the config file and returns the JSON from GET /v1/documents."""
    config = resolve_config(load_config(config_path))
    password = resolve_password(config)
    client = SmataClient(config.login_url, timezone=config.timezone)
    client.login(config.email, password)
    return client.get_json("/v1/documents", time_zone=client.timezone)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print the response of GET /v1/documents as JSON.")
    parser.add_argument("config", nargs="?", type=Path, default=DEFAULT_CONFIG, metavar="CONFIG",
                        help=f"TOML config file (default: {DEFAULT_CONFIG})")
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)

    try:
        response = fetch_documents(args.config)
    except ConfigError as e:
        print(f"smata-documents: {e}", file=sys.stderr)
        return 2
    except AuthError as e:
        print(f"smata-documents: {e}", file=sys.stderr)
        return 3
    except SmataError as e:
        print(f"smata-documents: {e}", file=sys.stderr)
        return 1

    json.dump(response, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

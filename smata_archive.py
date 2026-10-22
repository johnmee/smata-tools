"""Download and retain every document and invoice a SMATA owner account can see.

SMATA (a strata-management portal) has no bulk export, documents drop out of it without warning, and it can be very hard to find what you're looking for in their web UI.

This tool logs in as the owner, lists every document and invoice, fetches each one's detail, and downloads every file it refers to: the main file, the original and PDF variants, and invoice attachments.

It also saves the raw API metadata alongside.  Nothing local is ever deleted: items that stop being listed are marked "gone" but retained locally.

Only GET requests are made against the API.

    smata-archive [config.toml]

Programmatic use -- the library lives in the `smata` package, so this file is only
its command line:

    from smata import Archive, Client, load_config, resolve_config

    config = resolve_config(load_config(Path("config.toml")))
    client = Client(config.login_url, timezone=config.timezone)
    client.login(config.email, resolve_password(config))
    result = Archive(config.dest).sync(client)

CLI use:

    Run this file from the commandline for CLI usage instructions:
        python <thisfilename>.py

## Architecture

* `smata.client.SmataClient` handles the API connection: login, pagination, downloads.
* `smata.archive.DocumentsSource` talks to the /documents endpoint to list and download documents.
* `smata.archive.MetadataFile` is a local json file which maps between remote and local documents and captures the remote metadata.
* `smata.archive.Archive` manages the local files and data; providing synchronisation between the local and the remote.
* `smata.config` reads the TOML config; `smata.storage` is the SQLite document store.

"""
from __future__ import annotations

import sys

from smata.cli import main

if __name__ == "__main__":
    sys.exit(main())

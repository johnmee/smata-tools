"""The SMATA downloader package.

Everything the command line tools call lives here; the tools in the project root
(smata_archive.py, smata_documents.py, smata_jobs.py) are thin argument parsers over
these modules.

Layering, lowest first -- each module imports only from below it:

    version   no imports
    errors    no imports
    config    errors
    client    errors, version
    storage   no imports
    archive   client, errors
    cli       archive, client, config, errors, version

Programmatic use:

    from smata import Archive, Client, open_store, resolve_config

    config = resolve_config(load_config(Path("config.toml")))
    client = Client(config.login_url, timezone=config.timezone)
    client.login(config.email, resolve_password(config))
    result = Archive(config.dest).sync(client)
"""
from __future__ import annotations

from smata.archive import Archive, DocumentsSource, SyncResult
from smata.client import Client, SmataClient
from smata.config import Config, load_config, resolve_config, resolve_password
from smata.errors import ApiError, AuthError, ConfigError, SmataError
from smata.storage import create_database, get_document, mark_downloaded, open_store
from smata.version import __version__

__all__ = [
    "ApiError",
    "Archive",
    "AuthError",
    "Client",
    "Config",
    "ConfigError",
    "DocumentsSource",
    "SmataClient",
    "SmataError",
    "SyncResult",
    "__version__",
    "create_database",
    "get_document",
    "load_config",
    "mark_downloaded",
    "open_store",
    "resolve_config",
    "resolve_password",
]

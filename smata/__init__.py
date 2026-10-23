"""The SMATA downloader package.

Everything the command line tools call lives here; the tools in the project root
(smata_documents.py, smata_jobs.py) are thin argument parsers over
these modules.

Layering, lowest first -- each module imports only from below it:

    version   no imports
    errors    no imports
    config    errors
    session   no imports
    client    errors, session, version
    storage   no imports
    naming    no imports

Programmatic use:

    from smata import Client, open_store, resolve_config

    config = resolve_config(load_config(Path("config.toml")))
    client = Client(config.login_url, timezone=config.timezone)
    client.login(config.email, resolve_password(config))
"""
from __future__ import annotations

from smata.client import Client, SmataClient
from smata.config import Config, load_config, resolve_config, resolve_password
from smata.errors import ApiError, AuthError, ConfigError, SmataError
from smata.session import DEFAULT_SESSION_PATH
from smata.storage import (
    DB_FILENAME,
    create_database,
    get_document,
    list_documents,
    mark_downloaded,
    open_store,
)
from smata.version import __version__

__all__ = [
    "ApiError",
    "AuthError",
    "Client",
    "Config",
    "ConfigError",
    "DB_FILENAME",
    "DEFAULT_SESSION_PATH",
    "SmataClient",
    "SmataError",
    "__version__",
    "create_database",
    "get_document",
    "list_documents",
    "load_config",
    "mark_downloaded",
    "open_store",
    "resolve_config",
    "resolve_password",
]

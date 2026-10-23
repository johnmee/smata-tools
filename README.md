# SMATA DOWNLOADER

Download all the files from the SMATA strata document system.

## INSTALLATION

Clone the repository into a python environment.

    uv sync

## USAGE

* The easiest way is to copy the `config.example.toml` to `config.toml`, add your details, then run `python smata_documents.py` with no arguments to see the commands.

### Configuration and Authentication

Create a `config.toml` (see `config.example.toml`). The login url, email, password, destination folder (`dest`) and `timezone` are all set there; there are no command-line flags for them.

To protect the config details you should restrict read permissions with `chmod 600 config.toml`.  For more protection but less convenience you can omit the `password` configuration and be prompted for it instead.

File URLs are downloadable without a login: authentication only protects the metadata which returns lists of ID->URLs mappings.

### Commands

    python smata_documents.py index              # list documents from the API into <dest>/smata.db
    python smata_documents.py download           # index, then save the files that are not on disk yet
    python smata_documents.py list               # show what is stored, without the network

Each command reads `./config.toml`, or takes the path of another config file as its last argument. Run with no arguments to show the help.

`download` runs `index` first, so on a first run it is the only command you need. If the index step fails (for example a wrong password) nothing is downloaded. A file counts as downloaded only if it is recorded in the database and still exists in `dest`; a file you delete is fetched again on the next run. Failed downloads are reported and the command exits 1.

Only the first page of the documents listing is indexed so far.

### Re-use

Run `download` again; it picks up new documents and fetches only the files missing from `dest`.


## CODE LAYOUT

The library is the `smata` package. The files in the project root are thin command lines over it.

    smata/
      __init__.py   the public API, re-exported from the modules below
      version.py    __version__
      errors.py     SmataError, AuthError, ApiError, ConfigError
      config.py     Config, load_config, resolve_config, resolve_password
      session.py    the cached login token: load, save and clear it on disk
      client.py     SmataClient (login, pagination, download)
      storage.py    the SQLite store: create_database, add/update/get/mark_downloaded
      naming.py     main_filename, the name a downloaded file is given
      schema.sql    the store's DDL, shipped as package data

    smata_documents.py   the index, list and download commands

Modules only import from below them (`errors`, then `config`, then `session` and `client`; `storage` and `naming` import nothing from the package), so the dependency graph is a chain and not a cycle.
`tests/test_package.py` asserts that layering.

Programmatic use:

    from smata import Client, load_config, resolve_config, resolve_password

    config = resolve_config(load_config(Path("config.toml")))
    client = Client(config.login_url, timezone=config.timezone)
    client.login(config.email, resolve_password(config))
    page = client.get_json("/v1/documents", time_zone=client.timezone)


## DEVELOPMENT

### Tests

    uv run python -m unittest discover -s tests -t .

Everything runs against `tests/fakes.py`, a fake SMATA server, so the suite never touches
the real site or the network.


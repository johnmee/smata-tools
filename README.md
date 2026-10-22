# SMATA DOWNLOADER

Download all the files from the SMATA strata document system.

## INSTALLATION

Clone the repository into a python environment.

    uv sync

## USAGE

* The easiest way is to copy the `config.example.toml` to `config.toml`, add your details, then run `smata-archive` with no arguments. Use `smata-archive --help` for options.

### Configuration and Authentication

Create a `config.toml` (see `config.example.toml`). The login url, email, password, destination folder (`dest`) and `timezone` are all set there; there are no command-line flags for them.

To protect the config details you should restrict read permissions with `chmod 600 config.toml`.  For more protection but less convenience you can omit the `password` configuration and be prompted for it instead.

File URLs are downloadable without a login: authentication only protects the metadata which returns lists of ID->URLs mappings.

### First Use

    smata-archive              # reads ./config.toml
    smata-archive my.toml      # or name another config file

### Re-use

Run the same command again; only missing files are downloaded.

### Dry Run [-d] [--dry-run]

With a valid config, --dry-run does this:

It still does (network, read-only):
- Logs in with your email and password. This is a real login, and it prompts for the password if the config has none.
- Lists every document and invoice from the API (GET /v1/documents, two passes).
- Fetches detail (GET /v1/documents/{id}) for new or changed items, which is needed to retrieve their file URLs.
- Reads the existing archive folder to see which files are already on disk.

It skips (nothing is written):
- No file downloads.
- No .part cleanup.
- No updates to metadata/documents.json.
- No "gone" marking and no recovery of orphaned files.
- No entry in metadata/sync.json.
- It doesn't create dest or any subfolder. This relies on the run being read-only, and the existing test assertEqual(list(self.dest.iterdir()), []) covers it.

Output: it prints N would download files, N updated, N gone, N recovered, N errors, then lists each file path that would be downloaded. The gone count is computed but not saved. The recovered count is always 0, because orphan recovery only runs on a real sync.

Caveat: because detail isn't saved on a dry run, the next dry run (or real run) fetches it again. On a fresh archive, a dry run makes one detail request per document, so it isn't cheap.


## CODE LAYOUT

The library is the `smata` package. The files in the project root are thin command lines over it.

    smata/
      __init__.py   the public API, re-exported from the modules below
      version.py    __version__
      errors.py     SmataError, AuthError, ApiError, ConfigError
      config.py     Config, load_config, resolve_config, resolve_password
      client.py     SmataClient (login, pagination, download)
      storage.py    the SQLite store: create_database, add/update/get/mark_downloaded
      archive.py    Archive, DocumentsSource, SyncResult, MetadataFile
      cli.py        argparse and exit codes for smata-archive
      schema.sql    the store's DDL, shipped as package data

    smata_archive.py     the smata-archive command
    smata_documents.py   a one-page GET /v1/documents probe

Modules only import from below them (`errors`, then `config`, then `client`, then `storage`
and `archive`, then `cli`), so the dependency graph is a chain and not a cycle.
`tests/test_package.py` asserts that layering.

Programmatic use:

    from smata import Archive, Client, load_config, resolve_config

    config = resolve_config(load_config(Path("config.toml")))
    client = Client(config.login_url, timezone=config.timezone)
    client.login(config.email, resolve_password(config))
    result = Archive(config.dest).sync(client)


## DEVELOPMENT

### Tests

    uv run python -m unittest discover -s tests -t .

Everything runs against `tests/fakes.py`, a fake SMATA server, so the suite never touches
the real site or the network.


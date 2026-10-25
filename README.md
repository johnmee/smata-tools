# SMATA DOWNLOADER

Download all the files from the SMATA strata document system.

Owners in strata buildings using software provided by [Smata Technologies](https://www.smata.com) may find
the web interface slow, clumsy, a provider lock-in, and a general impediment to browsing the available documents
and gaining a better insight into what is happening in the scheme, and when.

This tools liberates the documents by downloading all available files and metadata onto the local hardware.

* Downloading files (documents and invoices) to local systems
* Capturing metadata associated with documents
* Detecting new files and changes to existing files
* Retaining removed files
* Migrating to other systems/providers/managers

## USAGE

## INSTALLATION

This is a python script which will use your personal website access (username/password) to call the same smata API that runs the web interface.  

You need the [Python](https://www.python.org/) programming language installed and the Python package manager [Astral UV](https://docs.astral.sh/uv/).

Clone this repository to your local environment and run

    uv sync

### Configuration and Authentication

> The easiest way is to copy the `config.example.toml` to `config.toml`, add your details, then run `python smata_documents.py` with no arguments to see the commands.

Copy the `config.example.toml`) to `config.toml` and complete the login url, email, password.  The destination folder (`dest`) and `timezone` are also configurable there.

To protect your password, consider restricting read permissions with `chmod 600 config.toml`; or, for more protection and less convenience, omit the `password` configuration and instead be prompted for it on very use.

### Commands

    python smata_documents.py index              # list documents from the API into <dest>/smata.db
    python smata_documents.py download           # index, then save the files that are not on disk yet
    python smata_documents.py list               # show what is stored, without the network

Each command reads `./config.toml`, or takes the path of another config file as its last argument. Run with no arguments to show the help.

`download` runs `index` first, so it is the only command you need. If the index step fails (for example a wrong password) nothing is downloaded. A file counts as downloaded only if it is recorded in the database and still exists in `dest`; a file you delete is fetched again on the next run. Failed downloads are reported and the command exits 1.

### Re-use

Run `download` repeatedly; it picks up new documents and fetches only the files missing from `dest`.


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

### TODO

The API offers a more functionality which we could tap into. See [smata-api](smata-api.md).  "Jobs" is of interest, but the usefulness of the smata software is heavily dependant upon the strata managers diligence in using it, so your mileage may vary.

Other functionality might be:
* meaningful invoice file names (extract the vendor name from invoices and price, add to filename)
* meaningful document file names (the date and categories are useful enough; perhaps not ideal to what they're about, but unfortunately the names my strata manager gives these files are effectively useless, if not obsfurcating)
* OCR of the downloaded files to create searchable contents
* RAG of the downloaded files to enable greater LLM insights

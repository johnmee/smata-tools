# SMATA DOWNLOADER

Download all the files from the SMATA strata document system.

## INSTALLATION

Clone the repository into a python environment.

✗  uv tree
Using CPython 3.14.3
Resolved 6 packages in 257ms
smata-archive v0.1.0
└── requests v2.34.2
    ├── certifi v2026.10.18
    ├── charset-normalizer v3.5.2
    ├── idna v3.20
    └── urllib3 v2.8.0


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


## DEVELOPMENT

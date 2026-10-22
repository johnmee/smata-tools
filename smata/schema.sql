-- SMATA document archive. One row per document/file.

CREATE TABLE documents (
    id              INTEGER PRIMARY KEY,   -- SMATA document id
    remote_filename TEXT    NOT NULL,      -- original upload name
    file_url        TEXT,                  -- SECRET: unauthenticated S3 URL
    local_filename  TEXT,                  -- relative to dest; NULL until downloaded
    display_name    TEXT,
    raw             TEXT    NOT NULL,      -- listing record as returned (JSON), latest seen
    category        TEXT,
    sp_number       TEXT    NOT NULL,
    is_invoice      INTEGER NOT NULL CHECK (is_invoice IN (0, 1)),
    created_at      INTEGER,               -- epoch seconds, as the API gives them
    downloaded_at   INTEGER,
    first_seen      INTEGER NOT NULL,
    last_seen       INTEGER NOT NULL
) STRICT;

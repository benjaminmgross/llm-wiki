"""Sqlite schema and connection helpers for the local mdwiki cache.

The database at ``.mdwiki/state.db`` is a derived cache. The source of truth is
``raw/`` + ``wiki/`` + ``wiki/log.md``; ``mdwiki rebuild`` reconstructs this file
from those three.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

EXPECTED_TABLES: frozenset[str] = frozenset(
    {
        "sources",
        "pages",
        "backrefs",
        "events",
        "embeddings",
        "transactions",
        "transaction_inverses",
    }
)

SCHEMA_SQL: str = """
CREATE TABLE IF NOT EXISTS sources (
    id              TEXT PRIMARY KEY,
    original_path   TEXT NOT NULL,
    raw_path        TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    mtime           REAL NOT NULL,
    ingested_at     REAL,
    status          TEXT NOT NULL CHECK (status IN ('pending', 'ingested', 'failed'))
);

CREATE TABLE IF NOT EXISTS pages (
    path            TEXT PRIMARY KEY,
    kind            TEXT NOT NULL CHECK (kind IN ('entity', 'concept', 'synthesis', 'index', 'log')),
    embedding       BLOB,
    last_touched_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS backrefs (
    page_path       TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    section_anchor  TEXT,
    quote           TEXT,
    confidence      REAL,
    FOREIGN KEY (page_path) REFERENCES pages(path) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_backrefs_source ON backrefs(source_id);
CREATE INDEX IF NOT EXISTS idx_backrefs_page ON backrefs(page_path);

CREATE TABLE IF NOT EXISTS events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    ts               REAL NOT NULL,
    kind             TEXT NOT NULL,
    source_id        TEXT,
    page_paths_json  TEXT,
    summary          TEXT,
    transaction_id   TEXT,
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE SET NULL,
    FOREIGN KEY (transaction_id) REFERENCES transactions(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS embeddings (
    text_hash       TEXT PRIMARY KEY,
    vector          BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    id                  TEXT PRIMARY KEY,
    ts                  REAL NOT NULL,
    undo_snapshot_path  TEXT,
    applied             INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS transaction_inverses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id  TEXT NOT NULL,
    sql             TEXT NOT NULL,
    params_json     TEXT NOT NULL DEFAULT '[]',
    FOREIGN KEY (transaction_id) REFERENCES transactions(id) ON DELETE CASCADE
);
"""


def init_db(db_path: Path) -> None:
    """Create the schema at ``db_path``, including any missing parent directories.

    Idempotent — re-applies ``CREATE TABLE IF NOT EXISTS`` for every table and
    runs inline migrations for any older wikis whose tables are missing newer
    columns.

    Parameters
    ----------
    db_path : Path
        Filesystem path where the sqlite database should live.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        _apply_inline_migrations(conn)


def _apply_inline_migrations(conn) -> None:
    """Add columns that newer code requires but older wikis don't yet have.

    Idempotent and cheap (PRAGMA + occasional ALTER). Skips silently if the
    target table doesn't yet exist (e.g. when called on a brand-new DB before
    schema creation).
    """
    inverse_cols_rows = conn.execute("PRAGMA table_info(transaction_inverses)").fetchall()
    if not inverse_cols_rows:
        return  # table doesn't exist yet; init_db will create it with the right schema
    inverse_cols = {row[1] for row in inverse_cols_rows}
    if "params_json" not in inverse_cols:
        conn.execute("ALTER TABLE transaction_inverses ADD COLUMN params_json TEXT NOT NULL DEFAULT '[]'")


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection with foreign keys, WAL journaling, and a busy timeout.

    WAL mode allows concurrent readers alongside one writer (instead of sqlite's
    default DELETE journaling, which serializes everything via exclusive locks).
    The 30s ``busy_timeout`` keeps a second writer waiting for the first to
    commit instead of failing instantly with ``database is locked``. Idempotent
    inline migrations are also applied here so existing wikis upgrade
    transparently when newer code reads their state.db.

    Parameters
    ----------
    db_path : Path
        Filesystem path to the sqlite database.

    Returns
    -------
    sqlite3.Connection
        A connection ready for use; safe to use as a context manager.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    # Cheap on every call: PRAGMA + occasional ALTER. Lets `pip install -U`
    # pick up new columns without forcing the user to re-init or rebuild.
    # Safe on brand-new DBs: _apply_inline_migrations is a no-op when target
    # tables don't yet exist (init_db creates them with the latest schema).
    _apply_inline_migrations(conn)
    return conn

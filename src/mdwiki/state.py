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
        # Phase 5 — Q1 quality compounding.
        "rejections",
        "cost_ledger",
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
    status          TEXT NOT NULL CHECK (status IN ('pending', 'ingested', 'failed')),
    failure_reason  TEXT
);

CREATE TABLE IF NOT EXISTS pages (
    path            TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    embedding       BLOB,
    last_touched_at REAL NOT NULL
    -- Page kind allowlist is enforced at the app layer by
    -- ``mdwiki.plan.allowed_kinds_for_wiki()``, which unions baseline kinds
    -- with the extras declared by the active profile in
    -- ``.mdwiki/config.toml``. The previous DB-level CHECK constraint was
    -- removed in Phase 5 to support corpus-aware profiles (initiative,
    -- transcripts, framework) without per-profile schema migrations.
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

-- Phase 5: rejection memory. Every plan rejection (verdict-based or quote-
-- anchor failure) records a row here so subsequent ingests of the same source
-- can inject prior reasons into the prompt.
CREATE TABLE IF NOT EXISTS rejections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id   TEXT NOT NULL,
    reason      TEXT NOT NULL,
    verdict     TEXT,
    ts          REAL NOT NULL,
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_rejections_source ON rejections(source_id);
CREATE INDEX IF NOT EXISTS idx_rejections_ts ON rejections(ts);

-- Native batch bootstrap appends an estimated-upper-bound receipt; its daily
-- cap (config.toml [cost_guard].daily_budget_usd) is enforced by check_budget().
CREATE TABLE IF NOT EXISTS cost_ledger (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    operation   TEXT NOT NULL,
    tokens_in   INTEGER,
    tokens_out  INTEGER,
    cost_usd    REAL NOT NULL,
    source_id   TEXT,
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_cost_ledger_ts ON cost_ledger(ts);
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

    Concurrent-safe: two processes can both observe the column missing and
    both attempt the ALTER. The second one fails with
    ``OperationalError: duplicate column name`` — we swallow exactly that
    case, since it means another connection already applied the migration.
    """
    source_cols_rows = conn.execute("PRAGMA table_info(sources)").fetchall()
    source_cols = {row[1] for row in source_cols_rows}
    if source_cols_rows and "failure_reason" not in source_cols:
        try:
            conn.execute("ALTER TABLE sources ADD COLUMN failure_reason TEXT")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise

    inverse_cols_rows = conn.execute("PRAGMA table_info(transaction_inverses)").fetchall()
    if not inverse_cols_rows:
        return  # table doesn't exist yet; init_db will create it with the right schema
    inverse_cols = {row[1] for row in inverse_cols_rows}
    if "params_json" not in inverse_cols:
        try:
            conn.execute("ALTER TABLE transaction_inverses ADD COLUMN params_json TEXT NOT NULL DEFAULT '[]'")
        except sqlite3.OperationalError as exc:
            # Another connection won the race and added the column first.
            # Any other OperationalError (locked DB, permission, malformed
            # SQL) must still surface.
            if "duplicate column" not in str(exc).lower():
                raise

    # Phase 5: drop the legacy ``pages.kind`` CHECK constraint so corpus-aware
    # profiles (initiative, transcripts, framework) can write profile-specific
    # page kinds. Page-kind validation now lives in the app layer
    # (``mdwiki.plan.allowed_kinds_for_wiki``). SQLite has no
    # ``ALTER TABLE DROP CONSTRAINT`` until very recent versions, so the
    # standard portable pattern is rebuild-via-temp-table.
    #
    # CRITICAL: ``DROP TABLE pages`` while ``foreign_keys = ON`` cascade-deletes
    # every ``backrefs`` row (FK is ``ON DELETE CASCADE``), silently destroying
    # all citation data on v1.1 → v1.2 upgrade. We disable FKs around the
    # rebuild per the SQLite recipe at
    # https://www.sqlite.org/lang_altertable.html#otheralter and re-enable in a
    # ``finally`` so a mid-rebuild failure can't leave the connection with FKs
    # silently disabled.
    pages_sql_row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='pages'").fetchone()
    if pages_sql_row is not None and "CHECK" in (pages_sql_row[0] or ""):
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            # ``DROP TABLE IF EXISTS pages_new`` runs OUTSIDE the BEGIN so a
            # prior crash that left ``pages_new`` on disk (SIGKILL or
            # disk-full mid-script with original ``pages`` intact) doesn't
            # turn the next migration into "table pages_new already exists"
            # and lock the user out of the wiki on every subsequent open.
            conn.executescript(
                """
                DROP TABLE IF EXISTS pages_new;
                BEGIN;
                CREATE TABLE pages_new (
                    path            TEXT PRIMARY KEY,
                    kind            TEXT NOT NULL,
                    embedding       BLOB,
                    last_touched_at REAL NOT NULL
                );
                INSERT INTO pages_new (path, kind, embedding, last_touched_at)
                    SELECT path, kind, embedding, last_touched_at FROM pages;
                DROP TABLE pages;
                ALTER TABLE pages_new RENAME TO pages;
                COMMIT;
                """
            )
        finally:
            conn.execute("PRAGMA foreign_keys = ON")


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

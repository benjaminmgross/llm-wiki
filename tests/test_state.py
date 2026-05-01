"""Tests for ``mdwiki.state`` — sqlite schema + connection helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from mdwiki.state import EXPECTED_TABLES, connect, init_db


@pytest.mark.unit
def test_init_db_creates_file(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    assert not db_path.exists()
    init_db(db_path)
    assert db_path.exists()


@pytest.mark.unit
def test_init_db_creates_all_expected_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    actual = {row[0] for row in rows}
    assert EXPECTED_TABLES.issubset(actual), f"missing tables: {EXPECTED_TABLES - actual}"


@pytest.mark.unit
def test_init_db_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_db(db_path)
    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    assert {row[0] for row in rows}.issuperset(EXPECTED_TABLES)


@pytest.mark.unit
def test_sources_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sources (id, original_path, raw_path, content_hash, mtime, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("abc123def456", "ai-agents/foo.md", "raw/abc123def456-foo.md", "abc123def456" + "0" * 52, 1700000000.0, "pending"),
        )
        row = conn.execute("SELECT id, original_path, status FROM sources WHERE id = ?", ("abc123def456",)).fetchone()
    assert tuple(row) == ("abc123def456", "ai-agents/foo.md", "pending")


@pytest.mark.unit
def test_connect_enables_foreign_keys(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_db(db_path)
    with connect(db_path) as conn:
        result = conn.execute("PRAGMA foreign_keys").fetchone()
    assert result[0] == 1


@pytest.mark.unit
def test_connect_returns_row_factory_dict_like(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sources (id, original_path, raw_path, content_hash, mtime, status) VALUES (?, ?, ?, ?, ?, ?)",
            ("xyz", "p", "r", "h" * 64, 1.0, "pending"),
        )
        row = conn.execute("SELECT * FROM sources WHERE id = 'xyz'").fetchone()
    assert row["id"] == "xyz"
    assert row["status"] == "pending"


@pytest.mark.unit
def test_init_db_creates_parent_dir(tmp_path: Path) -> None:
    db_path = tmp_path / "subdir" / "state.db"
    init_db(db_path)
    assert db_path.exists()


@pytest.mark.unit
def test_pages_table_supports_blob_embedding(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_db(db_path)
    blob = b"\x00\x01\x02fake-vector-bytes"
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO pages (path, kind, embedding, last_touched_at) VALUES (?, ?, ?, ?)",
            ("wiki/concepts/foo.md", "concept", blob, 1700000000.0),
        )
        row = conn.execute("SELECT embedding FROM pages WHERE path = 'wiki/concepts/foo.md'").fetchone()
    assert row[0] == blob


@pytest.mark.unit
def test_legacy_pages_check_constraint_migration_preserves_backrefs(tmp_path: Path) -> None:
    """v1.1 → v1.2 inline migration must NOT cascade-delete backrefs.

    Regression: prior versions of ``_apply_inline_migrations`` ran
    ``DROP TABLE pages`` while ``foreign_keys = ON``, which cascade-deletes
    every ``backrefs`` row (FK is ``ON DELETE CASCADE``). The fix wraps the
    rebuild in ``PRAGMA foreign_keys = OFF`` per the SQLite portable-migration
    recipe.
    """
    db_path = tmp_path / "state.db"
    # Hand-craft a legacy v1.1 schema that includes the CHECK constraint the
    # migration is designed to drop, plus the same backrefs FK as today.
    legacy_schema = """
    CREATE TABLE pages (
        path            TEXT PRIMARY KEY,
        kind            TEXT NOT NULL CHECK (kind IN ('entity', 'concept', 'synthesis', 'index', 'log')),
        embedding       BLOB,
        last_touched_at REAL NOT NULL
    );
    CREATE TABLE sources (
        id              TEXT PRIMARY KEY,
        original_path   TEXT NOT NULL,
        raw_path        TEXT NOT NULL,
        content_hash    TEXT NOT NULL,
        mtime           REAL NOT NULL,
        ingested_at     REAL,
        status          TEXT NOT NULL CHECK (status IN ('pending', 'ingested', 'failed'))
    );
    CREATE TABLE backrefs (
        page_path       TEXT NOT NULL,
        source_id       TEXT NOT NULL,
        section_anchor  TEXT,
        quote           TEXT,
        confidence      REAL,
        FOREIGN KEY (page_path) REFERENCES pages(path) ON DELETE CASCADE,
        FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
    );
    CREATE TABLE transactions (
        id                  TEXT PRIMARY KEY,
        ts                  REAL NOT NULL,
        undo_snapshot_path  TEXT,
        applied             INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE transaction_inverses (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id  TEXT NOT NULL,
        sql             TEXT NOT NULL,
        params_json     TEXT NOT NULL DEFAULT '[]',
        FOREIGN KEY (transaction_id) REFERENCES transactions(id) ON DELETE CASCADE
    );
    """
    bootstrap = sqlite3.connect(db_path)
    try:
        bootstrap.executescript(legacy_schema)
        bootstrap.execute(
            "INSERT INTO sources (id, original_path, raw_path, content_hash, mtime, status) VALUES (?, ?, ?, ?, ?, ?)",
            ("src1", "doc.md", "raw/src1.md", "h" * 64, 1.0, "ingested"),
        )
        bootstrap.execute(
            "INSERT INTO pages (path, kind, embedding, last_touched_at) VALUES (?, ?, ?, ?)",
            ("wiki/concepts/foo.md", "concept", None, 1.0),
        )
        bootstrap.execute(
            "INSERT INTO backrefs (page_path, source_id, section_anchor, quote, confidence) VALUES (?, ?, ?, ?, ?)",
            ("wiki/concepts/foo.md", "src1", "Intro", "the cited quote", 0.9),
        )
        bootstrap.commit()
    finally:
        bootstrap.close()

    # connect() runs _apply_inline_migrations, which detects the CHECK and
    # rebuilds the pages table. With FKs ON during DROP TABLE pages the
    # backrefs row would be cascade-deleted; the fix is to disable FKs around
    # the rebuild.
    with connect(db_path) as conn:
        backrefs_rows = conn.execute("SELECT page_path, quote FROM backrefs").fetchall()
        pages_rows = conn.execute("SELECT path, kind FROM pages").fetchall()
        # Confirm the CHECK constraint was actually dropped (the migration ran).
        pages_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='pages'"
        ).fetchone()[0]

    assert len(backrefs_rows) == 1, "backrefs row was deleted by the migration"
    assert backrefs_rows[0]["quote"] == "the cited quote"
    assert len(pages_rows) == 1
    assert pages_rows[0]["path"] == "wiki/concepts/foo.md"
    assert "CHECK" not in pages_sql, "migration did not drop the CHECK constraint"


@pytest.mark.unit
def test_transactions_link_inverses_via_fk(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    init_db(db_path)
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO transactions (id, ts, undo_snapshot_path, applied) VALUES (?, ?, ?, ?)",
            ("tx-1", 1.0, ".mdwiki/undo/tx-1", 1),
        )
        conn.execute(
            "INSERT INTO transaction_inverses (transaction_id, sql) VALUES (?, ?)",
            ("tx-1", "DELETE FROM pages WHERE path = 'wiki/foo.md'"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO transaction_inverses (transaction_id, sql) VALUES (?, ?)",
                ("tx-missing", "DELETE FROM x"),
            )

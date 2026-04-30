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

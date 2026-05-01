"""Tests for ``mdwiki.rejection_memory`` — per-source rejection tracking.

When an ingest plan is rejected (verdict-based or quote-anchor failure), the
reason is logged to ``state.db.rejections``. Subsequent ingests of the same
source can fetch the last-N reasons and inject them into the user prompt so
the LLM doesn't make the same mistake twice.

Phase 5 ships the primitives. Prompt-injection wiring is a documented
follow-up.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mdwiki.rejection_memory import last_n_reasons, record_rejection
from mdwiki.state import connect, init_db


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Initialize a fresh state.db so the rejections table exists."""
    db = tmp_path / "state.db"
    init_db(db)
    # Seed a source row so foreign-key references resolve.
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO sources (id, original_path, raw_path, content_hash, mtime, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("abc123abc123", "src.md", "raw/x.md", "abc" * 16, 1.0, "pending"),
        )
        conn.commit()
    return db


@pytest.mark.unit
def test_record_rejection_inserts_row(db_path: Path) -> None:
    with connect(db_path) as conn:
        record_rejection(conn=conn, source_id="abc123abc123", reason="low-quality: insufficient citations", verdict="low-quality")
        conn.commit()
        rows = conn.execute("SELECT source_id, reason, verdict FROM rejections").fetchall()
    assert len(rows) == 1
    assert rows[0]["source_id"] == "abc123abc123"
    assert rows[0]["reason"] == "low-quality: insufficient citations"
    assert rows[0]["verdict"] == "low-quality"


@pytest.mark.unit
def test_last_n_reasons_returns_most_recent_first(db_path: Path) -> None:
    with connect(db_path) as conn:
        record_rejection(conn=conn, source_id="abc123abc123", reason="first reason", verdict="low-quality")
        record_rejection(conn=conn, source_id="abc123abc123", reason="second reason", verdict="low-quality")
        record_rejection(conn=conn, source_id="abc123abc123", reason="third reason", verdict="out-of-scope")
        conn.commit()

        reasons = last_n_reasons(conn=conn, source_id="abc123abc123", n=10)
    assert reasons == ["third reason", "second reason", "first reason"]


@pytest.mark.unit
def test_last_n_reasons_caps_at_n(db_path: Path) -> None:
    with connect(db_path) as conn:
        for i in range(10):
            record_rejection(conn=conn, source_id="abc123abc123", reason=f"reason {i}", verdict="low-quality")
        conn.commit()
        reasons = last_n_reasons(conn=conn, source_id="abc123abc123", n=3)
    assert len(reasons) == 3
    assert reasons[0] == "reason 9"  # most recent first


@pytest.mark.unit
def test_last_n_reasons_empty_for_source_with_no_rejections(db_path: Path) -> None:
    with connect(db_path) as conn:
        # No rejections inserted; method should return [].
        reasons = last_n_reasons(conn=conn, source_id="abc123abc123", n=5)
    assert reasons == []

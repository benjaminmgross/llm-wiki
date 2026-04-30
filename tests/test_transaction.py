"""Tests for ``mdwiki.transaction`` — atomic ingest tx with per-tx undo snapshot."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mdwiki.init import init_wiki
from mdwiki.state import connect
from mdwiki.transaction import IngestTransaction, TransactionAborted


@pytest.fixture
def wiki(tmp_path: Path) -> Path:
    (tmp_path / "src.md").write_text("# Source\nbody")
    init_wiki(tmp_path)
    return tmp_path


@pytest.mark.unit
def test_successful_tx_writes_file(wiki: Path) -> None:
    target = wiki / "wiki" / "concepts" / "foo.md"
    with IngestTransaction(wiki_root=wiki, source_id=None, summary="test") as tx:
        tx.write_file(target, "## Foo\n\nhello")
    assert target.read_text() == "## Foo\n\nhello"


@pytest.mark.unit
def test_successful_tx_records_in_transactions_table(wiki: Path) -> None:
    with IngestTransaction(wiki_root=wiki, source_id=None, summary="test commit") as tx:
        tx.write_file(wiki / "wiki" / "x.md", "x")

    db_path = wiki / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        rows = conn.execute("SELECT id, applied FROM transactions").fetchall()
    assert len(rows) == 1
    assert rows[0]["applied"] == 1


@pytest.mark.unit
def test_tx_records_event_on_commit(wiki: Path) -> None:
    with IngestTransaction(wiki_root=wiki, source_id=None, summary="ingested foo") as tx:
        tx.write_file(wiki / "wiki" / "x.md", "x")

    db_path = wiki / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        events = conn.execute("SELECT summary, kind, transaction_id FROM events").fetchall()
    assert len(events) == 1
    assert events[0]["summary"] == "ingested foo"
    assert events[0]["kind"] == "ingest"
    assert events[0]["transaction_id"] is not None


@pytest.mark.unit
def test_tx_appends_to_log(wiki: Path) -> None:
    with IngestTransaction(wiki_root=wiki, source_id=None, summary="ingested foo") as tx:
        tx.write_file(wiki / "wiki" / "x.md", "x")

    log = (wiki / "wiki" / "log.md").read_text()
    assert "ingested foo" in log


@pytest.mark.unit
def test_tx_snapshots_existing_file_for_undo(wiki: Path) -> None:
    target = wiki / "wiki" / "page.md"
    target.write_text("ORIGINAL")

    with IngestTransaction(wiki_root=wiki, source_id=None, summary="overwrite") as tx:
        tx.write_file(target, "MODIFIED")

    db_path = wiki / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        tx_row = conn.execute("SELECT id, undo_snapshot_path FROM transactions").fetchone()
    snapshot_dir = wiki / ".mdwiki" / "undo" / tx_row["id"]
    assert snapshot_dir.is_dir()
    snapshot_files = list(snapshot_dir.rglob("*"))
    assert any(f.is_file() and f.read_text() == "ORIGINAL" for f in snapshot_files)


@pytest.mark.unit
def test_tx_marks_new_file_for_delete_on_undo(wiki: Path) -> None:
    target = wiki / "wiki" / "newpage.md"
    assert not target.exists()

    with IngestTransaction(wiki_root=wiki, source_id=None, summary="create") as tx:
        tx.write_file(target, "CREATED")

    db_path = wiki / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        tx_row = conn.execute("SELECT id FROM transactions").fetchone()
    snapshot_dir = wiki / ".mdwiki" / "undo" / tx_row["id"]
    deletion_markers = list(snapshot_dir.glob("__delete_on_undo__/**/*"))
    assert any(f.is_file() for f in deletion_markers)


@pytest.mark.unit
def test_tx_rollback_on_exception_does_not_write_file(wiki: Path) -> None:
    target = wiki / "wiki" / "doomed.md"

    with pytest.raises(RuntimeError):
        with IngestTransaction(wiki_root=wiki, source_id=None, summary="oops") as tx:
            tx.write_file(target, "partial write")
            raise RuntimeError("simulated failure")

    assert not target.exists()


@pytest.mark.unit
def test_tx_rollback_on_exception_does_not_record_event(wiki: Path) -> None:
    with pytest.raises(RuntimeError):
        with IngestTransaction(wiki_root=wiki, source_id=None, summary="oops") as tx:
            tx.write_file(wiki / "wiki" / "x.md", "x")
            raise RuntimeError("simulated failure")

    db_path = wiki / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        events_count = conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]
        tx_count = conn.execute("SELECT COUNT(*) AS c FROM transactions").fetchone()["c"]
    assert events_count == 0
    assert tx_count == 0


@pytest.mark.unit
def test_explicit_abort_rolls_back(wiki: Path) -> None:
    target = wiki / "wiki" / "page.md"
    target.write_text("ORIGINAL")

    with pytest.raises(TransactionAborted):
        with IngestTransaction(wiki_root=wiki, source_id=None, summary="abort me") as tx:
            tx.write_file(target, "WOULD BE NEW")
            tx.abort("user rejected the plan")

    assert target.read_text() == "ORIGINAL"


@pytest.mark.unit
def test_upsert_page_update_branch_does_not_crash_on_commit(wiki: Path) -> None:
    """Regression: round-2 C1.

    Round-1 refactor recorded ``prev["embedding"]`` (bytes) into the inverse
    for the UPDATE branch. ``_commit`` then ``json.dumps``-es those params,
    which raises ``TypeError: Object of type bytes is not JSON serializable``.
    The fix is to drop the bytes from the inverse — embeddings are derived
    from content and cheap to re-compute on the next read.

    This test exercises the full insert-then-update-then-commit path that
    triggered the production crash.
    """
    embedding_bytes = b"\x01\x02\x03\x04" * 96  # arbitrary blob, simulates a real embedding
    page_path = "wiki/concepts/foo.md"

    # First tx: INSERT branch. Should commit cleanly.
    with IngestTransaction(wiki_root=wiki, source_id=None, summary="insert page") as tx:
        tx.write_file(wiki / page_path, "# Foo\n\nv1")
        tx.upsert_page(path=page_path, kind="concept", embedding=embedding_bytes, last_touched_at=1.0)

    # Second tx: UPDATE branch. Pre-fix, this raised TypeError during _commit.
    new_embedding = b"\xa0\xa1\xa2\xa3" * 96
    with IngestTransaction(wiki_root=wiki, source_id=None, summary="update page") as tx:
        tx.write_file(wiki / page_path, "# Foo\n\nv2")
        tx.upsert_page(path=page_path, kind="concept", embedding=new_embedding, last_touched_at=2.0)

    # Verify both txs committed and the second update stuck.
    db_path = wiki / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        page_row = conn.execute(
            "SELECT kind, embedding, last_touched_at FROM pages WHERE path = ?", (page_path,)
        ).fetchone()
        inverses = conn.execute(
            "SELECT sql, params_json FROM transaction_inverses ORDER BY id"
        ).fetchall()
    assert page_row["embedding"] == new_embedding
    assert page_row["last_touched_at"] == 2.0
    # Inverse for the UPDATE must NOT carry the prior bytes (so its params_json
    # round-trips cleanly through json.dumps/json.loads).
    update_inverses = [inv for inv in inverses if "UPDATE pages" in inv["sql"]]
    assert update_inverses, "expected at least one UPDATE inverse from the second tx"
    # Round-trip every params_json — pre-fix this would have crashed at write time.
    for inv in inverses:
        json.loads(inv["params_json"])  # must not raise


@pytest.mark.unit
def test_tx_with_source_id_marks_source_ingested(wiki: Path) -> None:
    db_path = wiki / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        source_id = conn.execute("SELECT id FROM sources LIMIT 1").fetchone()["id"]

    with IngestTransaction(wiki_root=wiki, source_id=source_id, summary="ingest src") as tx:
        tx.write_file(wiki / "wiki" / "x.md", "x")

    with connect(db_path) as conn:
        row = conn.execute("SELECT status, ingested_at FROM sources WHERE id = ?", (source_id,)).fetchone()
    assert row["status"] == "ingested"
    assert row["ingested_at"] is not None

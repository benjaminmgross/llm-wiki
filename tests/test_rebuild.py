"""Tests for ``mdwiki.rebuild`` — reconstruct ``state.db`` from raw/ + log.md."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mdwiki.discover import WikiNotFound
from mdwiki.init import init_wiki
from mdwiki.rebuild import RebuildError, rebuild_wiki
from mdwiki.state import connect


@pytest.fixture
def initialized_wiki(tmp_path: Path) -> Path:
    (tmp_path / "alpha.md").write_text("# Alpha")
    (tmp_path / "beta.md").write_text("# Beta")
    (tmp_path / "gamma.md").write_text("# Gamma")
    init_wiki(tmp_path)
    return tmp_path


@pytest.mark.unit
def test_rebuild_restores_sources_after_state_db_deleted(initialized_wiki: Path) -> None:
    db_path = initialized_wiki / ".mdwiki" / "state.db"
    db_path.unlink()
    assert not db_path.exists()

    result = rebuild_wiki(initialized_wiki)

    assert db_path.exists()
    assert result.sources_restored == 3


@pytest.mark.unit
def test_rebuild_preserves_original_paths_via_sidecar(initialized_wiki: Path) -> None:
    db_path = initialized_wiki / ".mdwiki" / "state.db"
    db_path.unlink()

    rebuild_wiki(initialized_wiki)

    with connect(db_path) as conn:
        rows = conn.execute("SELECT original_path FROM sources ORDER BY original_path").fetchall()
    assert [row["original_path"] for row in rows] == ["alpha.md", "beta.md", "gamma.md"]


@pytest.mark.unit
def test_rebuild_marks_restored_sources_as_pending(initialized_wiki: Path) -> None:
    db_path = initialized_wiki / ".mdwiki" / "state.db"
    db_path.unlink()
    rebuild_wiki(initialized_wiki)
    with connect(db_path) as conn:
        statuses = {row["status"] for row in conn.execute("SELECT status FROM sources")}
    assert statuses == {"pending"}


@pytest.mark.unit
def test_rebuild_restores_ingested_status_from_sidecar(initialized_wiki: Path) -> None:
    sidecar_path = initialized_wiki / "raw" / ".sources.json"
    sidecar = json.loads(sidecar_path.read_text())
    alpha_id = next(source_id for source_id, meta in sidecar.items() if meta["original_path"] == "alpha.md")
    sidecar[alpha_id]["status"] = "ingested"
    sidecar[alpha_id]["ingested_at"] = 123.45
    sidecar_path.write_text(json.dumps(sidecar, indent=2, sort_keys=True))

    db_path = initialized_wiki / ".mdwiki" / "state.db"
    db_path.unlink()
    rebuild_wiki(initialized_wiki)

    with connect(db_path) as conn:
        restored = {
            row["original_path"]: (row["status"], row["ingested_at"])
            for row in conn.execute("SELECT original_path, status, ingested_at FROM sources")
        }
    assert restored["alpha.md"] == ("ingested", 123.45)
    assert restored["beta.md"] == ("pending", None)
    assert restored["gamma.md"] == ("pending", None)


@pytest.mark.unit
def test_rebuild_infers_ingested_status_from_log_for_legacy_sidecar(initialized_wiki: Path) -> None:
    (initialized_wiki / "wiki" / "log.md").write_text(
        "- 2026-05-03T03:11:03Z [tx-alpha] ingest alpha.md → 0 update(s), 1 new page(s) [batch]\n"
    )

    db_path = initialized_wiki / ".mdwiki" / "state.db"
    db_path.unlink()
    rebuild_wiki(initialized_wiki)

    with connect(db_path) as conn:
        restored = {
            row["original_path"]: row["status"]
            for row in conn.execute("SELECT original_path, status FROM sources")
        }
    assert restored["alpha.md"] == "ingested"
    assert restored["beta.md"] == "pending"
    assert restored["gamma.md"] == "pending"


@pytest.mark.unit
def test_rebuild_does_not_infer_undone_ingest_from_log(initialized_wiki: Path) -> None:
    (initialized_wiki / "wiki" / "log.md").write_text(
        "- 2026-05-03T03:11:03Z [tx-alpha] ingest alpha.md → 0 update(s), 1 new page(s) [batch]\n"
        "- 2026-05-03T03:12:03Z [undo] reverted tx-alpha\n"
    )

    db_path = initialized_wiki / ".mdwiki" / "state.db"
    db_path.unlink()
    rebuild_wiki(initialized_wiki)

    with connect(db_path) as conn:
        status = conn.execute("SELECT status FROM sources WHERE original_path = 'alpha.md'").fetchone()["status"]
    assert status == "pending"


@pytest.mark.unit
def test_rebuild_is_idempotent(initialized_wiki: Path) -> None:
    rebuild_wiki(initialized_wiki)
    rebuild_wiki(initialized_wiki)
    db_path = initialized_wiki / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM sources").fetchone()["c"]
    assert count == 3


@pytest.mark.unit
def test_rebuild_raises_when_no_wiki(tmp_path: Path) -> None:
    with pytest.raises(WikiNotFound):
        rebuild_wiki(tmp_path)


@pytest.mark.unit
def test_rebuild_raises_when_sidecar_missing(initialized_wiki: Path) -> None:
    sidecar = initialized_wiki / "raw" / ".sources.json"
    sidecar.unlink()
    (initialized_wiki / ".mdwiki" / "state.db").unlink()
    with pytest.raises(RebuildError) as excinfo:
        rebuild_wiki(initialized_wiki)
    assert ".sources.json" in str(excinfo.value)


@pytest.mark.unit
def test_rebuild_handles_partial_state_db_loss_via_upsert(initialized_wiki: Path) -> None:
    db_path = initialized_wiki / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        conn.execute("DELETE FROM sources")
        conn.commit()

    result = rebuild_wiki(initialized_wiki)

    assert result.sources_restored == 3
    with connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM sources").fetchone()["c"]
    assert count == 3


@pytest.mark.unit
def test_rebuild_returns_event_replay_count_zero_when_log_missing(initialized_wiki: Path) -> None:
    result = rebuild_wiki(initialized_wiki)
    assert result.events_replayed == 0

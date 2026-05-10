"""Tests for ``mdwiki.undo`` — atomic rollback of the last N transactions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from mdwiki.ingest import ingest_source
from mdwiki.init import init_wiki
from mdwiki.llm.base import CompleteResult
from mdwiki.state import connect
from mdwiki.undo import UndoError, undo_last


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")


def _good_plan_json(
    *,
    page_path: str = "wiki/concepts/c.md",
    section_id: str = "ai.md/Intro",
    quote: str = "this paper introduces attention sinks for long contexts",
) -> str:
    return json.dumps(
        {
            "verdict": "ingest",
            "rationale": "x",
            "updates": [],
            "new_pages": [
                {
                    "path": page_path,
                    "kind": "concept",
                    "content": "# C\n\nbody",
                    "claims": [{"source_section_id": section_id, "quote": quote}],
                }
            ],
            "cross_refs": [],
        }
    )


@pytest.fixture
def wiki_with_one_ingested(tmp_path: Path, mocker: MockerFixture) -> Path:
    (tmp_path / "ai.md").write_text(
        "# A\n\n## Intro\n\nThis paper introduces attention sinks for long contexts.\n"
    )
    init_wiki(tmp_path)
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.complete",
        return_value=CompleteResult(text=_good_plan_json(), input_tokens=10, output_tokens=10),
    )
    ingest_source(tmp_path, "ai.md", yes=True)
    return tmp_path


@pytest.mark.unit
def test_undo_last_with_no_applied_tx_returns_empty_result(tmp_path: Path) -> None:
    init_wiki(tmp_path)
    result = undo_last(tmp_path, n=1)
    assert result.undone_count == 0
    assert "no applied" in result.message.lower() or "nothing" in result.message.lower()


@pytest.mark.unit
def test_undo_removes_wiki_file_added_by_ingest(wiki_with_one_ingested: Path) -> None:
    page = wiki_with_one_ingested / "wiki" / "concepts" / "c.md"
    assert page.is_file()

    undo_last(wiki_with_one_ingested, n=1)

    assert not page.exists()


@pytest.mark.unit
def test_undo_removes_pages_row(wiki_with_one_ingested: Path) -> None:
    db_path = wiki_with_one_ingested / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        before = conn.execute("SELECT COUNT(*) AS c FROM pages").fetchone()["c"]
    assert before == 1

    undo_last(wiki_with_one_ingested, n=1)

    with connect(db_path) as conn:
        after = conn.execute("SELECT COUNT(*) AS c FROM pages").fetchone()["c"]
    assert after == 0


@pytest.mark.unit
def test_undo_removes_backrefs(wiki_with_one_ingested: Path) -> None:
    db_path = wiki_with_one_ingested / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        before = conn.execute("SELECT COUNT(*) AS c FROM backrefs").fetchone()["c"]
    assert before > 0

    undo_last(wiki_with_one_ingested, n=1)

    with connect(db_path) as conn:
        after = conn.execute("SELECT COUNT(*) AS c FROM backrefs").fetchone()["c"]
    assert after == 0


@pytest.mark.unit
def test_undo_restores_source_to_pending(wiki_with_one_ingested: Path) -> None:
    db_path = wiki_with_one_ingested / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        before = conn.execute("SELECT status, ingested_at FROM sources WHERE original_path='ai.md'").fetchone()
    assert before["status"] == "ingested"
    assert before["ingested_at"] is not None

    undo_last(wiki_with_one_ingested, n=1)

    with connect(db_path) as conn:
        after = conn.execute("SELECT status, ingested_at FROM sources WHERE original_path='ai.md'").fetchone()
    assert after["status"] == "pending"
    assert after["ingested_at"] is None


@pytest.mark.unit
def test_undo_restores_source_sidecar_to_pending(wiki_with_one_ingested: Path) -> None:
    sidecar_path = wiki_with_one_ingested / "raw" / ".sources.json"
    sidecar_before = json.loads(sidecar_path.read_text())
    source_meta_before = next(meta for meta in sidecar_before.values() if meta["original_path"] == "ai.md")
    assert source_meta_before["status"] == "ingested"
    assert source_meta_before["ingested_at"] is not None

    undo_last(wiki_with_one_ingested, n=1)

    sidecar_after = json.loads(sidecar_path.read_text())
    source_meta_after = next(meta for meta in sidecar_after.values() if meta["original_path"] == "ai.md")
    assert source_meta_after["status"] == "pending"
    assert source_meta_after["ingested_at"] is None


@pytest.mark.unit
def test_undo_marks_transaction_unapplied(wiki_with_one_ingested: Path) -> None:
    db_path = wiki_with_one_ingested / ".mdwiki" / "state.db"
    undo_last(wiki_with_one_ingested, n=1)
    with connect(db_path) as conn:
        rows = conn.execute("SELECT applied FROM transactions").fetchall()
    assert all(row["applied"] == 0 for row in rows)


@pytest.mark.unit
def test_undo_appends_marker_to_log(wiki_with_one_ingested: Path) -> None:
    log_before = (wiki_with_one_ingested / "wiki" / "log.md").read_text()
    undo_last(wiki_with_one_ingested, n=1)
    log_after = (wiki_with_one_ingested / "wiki" / "log.md").read_text()
    assert len(log_after) > len(log_before)
    assert "undo" in log_after.lower()


@pytest.mark.unit
def test_undo_already_undone_tx_is_skipped(wiki_with_one_ingested: Path) -> None:
    """Calling undo twice doesn't double-undo."""
    first = undo_last(wiki_with_one_ingested, n=1)
    second = undo_last(wiki_with_one_ingested, n=1)
    assert first.undone_count == 1
    assert second.undone_count == 0


@pytest.mark.unit
def test_undo_n_equals_2_undoes_both(tmp_path: Path, mocker: MockerFixture) -> None:
    """Two ingests, then undo 2 → both rolled back."""
    (tmp_path / "ai.md").write_text("# A\n\n## Intro\n\nThis paper introduces attention sinks for long contexts.\n")
    (tmp_path / "ai2.md").write_text("# A2\n\n## Intro\n\nThis paper introduces attention sinks for long contexts.\n")
    init_wiki(tmp_path)
    plans = iter(
        [
            CompleteResult(text=_good_plan_json(page_path="wiki/concepts/p1.md", section_id="ai.md/Intro"), input_tokens=10, output_tokens=10),
            CompleteResult(text=_good_plan_json(page_path="wiki/concepts/p2.md", section_id="ai2.md/Intro"), input_tokens=10, output_tokens=10),
        ]
    )
    mocker.patch("mdwiki.llm.anthropic.AnthropicProvider.complete", side_effect=lambda **_kw: next(plans))
    ingest_source(tmp_path, "ai.md", yes=True)
    ingest_source(tmp_path, "ai2.md", yes=True)

    result = undo_last(tmp_path, n=2)

    assert result.undone_count == 2
    assert not (tmp_path / "wiki" / "concepts" / "p1.md").exists()
    assert not (tmp_path / "wiki" / "concepts" / "p2.md").exists()


@pytest.mark.unit
def test_undo_n_zero_or_negative_raises(wiki_with_one_ingested: Path) -> None:
    with pytest.raises(UndoError):
        undo_last(wiki_with_one_ingested, n=0)
    with pytest.raises(UndoError):
        undo_last(wiki_with_one_ingested, n=-1)


@pytest.mark.unit
def test_undo_outside_wiki_raises(tmp_path: Path) -> None:
    with pytest.raises(UndoError):
        undo_last(tmp_path, n=1)

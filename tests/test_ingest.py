"""Tests for ``mdwiki.ingest`` — orchestrator for one source."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from mdwiki.ingest import IngestError, ingest_source
from mdwiki.init import init_wiki
from mdwiki.llm.base import CompleteResult
from mdwiki.state import connect


@pytest.fixture
def wiki_with_one_source(tmp_path: Path) -> Path:
    (tmp_path / "ai.md").write_text(
        "# Attention\n\n## Intro\n\nThis paper introduces attention sinks for long contexts.\n\n## Method\n\nWe drop tokens after position 1024.\n"
    )
    init_wiki(tmp_path)
    return tmp_path


def _good_plan_json() -> str:
    return json.dumps(
        {
            "verdict": "ingest",
            "rationale": "One core concept, worth a new page.",
            "updates": [],
            "new_pages": [
                {
                    "path": "wiki/concepts/attention-sinks.md",
                    "kind": "concept",
                    "content": "# Attention Sinks\n\nA technique for long-context attention.",
                    "claims": [
                        {
                            "source_section_id": "ai.md/Intro",
                            "quote": "this paper introduces attention sinks for long contexts",
                        }
                    ],
                }
            ],
            "cross_refs": [],
        }
    )


def _mock_provider(mocker: MockerFixture, plan_json: str) -> None:
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.complete",
        return_value=CompleteResult(text=plan_json, input_tokens=100, output_tokens=50),
    )


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")


@pytest.mark.unit
def test_ingest_resolves_source_by_original_path(wiki_with_one_source: Path, mocker: MockerFixture) -> None:
    _mock_provider(mocker, _good_plan_json())
    result = ingest_source(wiki_with_one_source, "ai.md", yes=True)
    assert result.applied is True
    assert result.source_id is not None


@pytest.mark.unit
def test_ingest_resolves_source_by_short_hash(wiki_with_one_source: Path, mocker: MockerFixture) -> None:
    db_path = wiki_with_one_source / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        sid = conn.execute("SELECT id FROM sources LIMIT 1").fetchone()["id"]

    _mock_provider(mocker, _good_plan_json())
    result = ingest_source(wiki_with_one_source, sid[:6], yes=True)
    assert result.source_id == sid


@pytest.mark.unit
def test_ingest_refuses_unknown_source(wiki_with_one_source: Path) -> None:
    with pytest.raises(IngestError) as excinfo:
        ingest_source(wiki_with_one_source, "does-not-exist.md", yes=True)
    assert "not registered" in str(excinfo.value).lower() or "not found" in str(excinfo.value).lower()


@pytest.mark.unit
def test_ingest_strips_dot_slash_prefix(wiki_with_one_source: Path, mocker: MockerFixture) -> None:
    _mock_provider(mocker, _good_plan_json())
    result = ingest_source(wiki_with_one_source, "./ai.md", yes=True)
    assert result.applied is True


@pytest.mark.unit
def test_ingest_normalizes_zsh_escaped_spaces(tmp_path: Path, mocker: MockerFixture) -> None:
    """Users who quote AND backslash-escape (a zsh tarpit) get literal `\\ ` in the path."""
    src = tmp_path / "with spaces.md"
    src.write_text("# x\n\n## body\n\nthis paper introduces attention sinks for long contexts\n")
    init_wiki(tmp_path)

    plan_json = json.dumps(
        {
            "verdict": "ingest",
            "rationale": "x",
            "updates": [],
            "new_pages": [
                {
                    "path": "wiki/concepts/x.md",
                    "kind": "concept",
                    "content": "...",
                    "claims": [
                        {
                            "source_section_id": "with spaces.md/body",
                            "quote": "this paper introduces attention sinks for long contexts",
                        }
                    ],
                }
            ],
            "cross_refs": [],
        }
    )
    _mock_provider(mocker, plan_json)

    result = ingest_source(tmp_path, r"with\ spaces.md", yes=True)
    assert result.applied is True


@pytest.mark.unit
def test_ingest_resolves_filesystem_path(tmp_path: Path, mocker: MockerFixture) -> None:
    """Tab-completion gives a real filesystem path; resolve it to the registered source."""
    sub = tmp_path / "sub"
    sub.mkdir()
    src = sub / "doc.md"
    src.write_text("# x\n\n## body\n\nattention sinks reduce drift in long contexts\n")
    init_wiki(tmp_path)

    plan_json = json.dumps(
        {
            "verdict": "ingest",
            "rationale": "x",
            "updates": [],
            "new_pages": [
                {
                    "path": "wiki/concepts/x.md",
                    "kind": "concept",
                    "content": "...",
                    "claims": [
                        {
                            "source_section_id": "sub/doc.md/body",
                            "quote": "attention sinks reduce drift in long contexts",
                        }
                    ],
                }
            ],
            "cross_refs": [],
        }
    )
    _mock_provider(mocker, plan_json)

    result = ingest_source(tmp_path, str(src), yes=True)
    assert result.applied is True


@pytest.mark.unit
def test_ingest_unknown_source_suggests_close_matches(wiki_with_one_source: Path) -> None:
    with pytest.raises(IngestError) as excinfo:
        ingest_source(wiki_with_one_source, "ai", yes=True)
    msg = str(excinfo.value)
    assert "ai.md" in msg  # the close match should be suggested


@pytest.mark.unit
def test_ingest_idempotent_for_already_ingested_source(wiki_with_one_source: Path, mocker: MockerFixture) -> None:
    _mock_provider(mocker, _good_plan_json())
    first = ingest_source(wiki_with_one_source, "ai.md", yes=True)
    assert first.applied is True

    second = ingest_source(wiki_with_one_source, "ai.md", yes=True)
    assert second.applied is False
    assert "already" in second.message.lower()


@pytest.mark.unit
def test_ingest_aborts_when_quote_verification_fails(wiki_with_one_source: Path, mocker: MockerFixture) -> None:
    bad_plan = json.dumps(
        {
            "verdict": "ingest",
            "rationale": "x",
            "updates": [],
            "new_pages": [
                {
                    "path": "wiki/concepts/x.md",
                    "kind": "concept",
                    "content": "...",
                    "claims": [
                        {"source_section_id": "ai.md/Intro", "quote": "this quote was never in the source"}
                    ],
                }
            ],
            "cross_refs": [],
        }
    )
    _mock_provider(mocker, bad_plan)
    with pytest.raises(IngestError) as excinfo:
        ingest_source(wiki_with_one_source, "ai.md", yes=True)
    assert "quote" in str(excinfo.value).lower()


@pytest.mark.unit
def test_ingest_does_not_apply_when_user_rejects(wiki_with_one_source: Path, mocker: MockerFixture) -> None:
    _mock_provider(mocker, _good_plan_json())
    result = ingest_source(wiki_with_one_source, "ai.md", yes=False, confirm=lambda _: False)
    assert result.applied is False
    assert "reject" in result.message.lower() or "abort" in result.message.lower()
    assert not (wiki_with_one_source / "wiki" / "concepts" / "attention-sinks.md").exists()


@pytest.mark.unit
def test_ingest_applies_writes_wiki_file(wiki_with_one_source: Path, mocker: MockerFixture) -> None:
    _mock_provider(mocker, _good_plan_json())
    ingest_source(wiki_with_one_source, "ai.md", yes=True)
    page = wiki_with_one_source / "wiki" / "concepts" / "attention-sinks.md"
    assert page.is_file()
    assert "Attention Sinks" in page.read_text()


@pytest.mark.unit
def test_ingest_marks_source_ingested_in_db(wiki_with_one_source: Path, mocker: MockerFixture) -> None:
    _mock_provider(mocker, _good_plan_json())
    ingest_source(wiki_with_one_source, "ai.md", yes=True)

    db_path = wiki_with_one_source / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        row = conn.execute("SELECT status, ingested_at FROM sources WHERE original_path='ai.md'").fetchone()
    assert row["status"] == "ingested"
    assert row["ingested_at"] is not None


@pytest.mark.unit
def test_ingest_records_backref_for_each_claim(wiki_with_one_source: Path, mocker: MockerFixture) -> None:
    _mock_provider(mocker, _good_plan_json())
    ingest_source(wiki_with_one_source, "ai.md", yes=True)

    db_path = wiki_with_one_source / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        rows = conn.execute("SELECT page_path, source_id, quote FROM backrefs").fetchall()
    assert len(rows) == 1
    assert rows[0]["page_path"] == "wiki/concepts/attention-sinks.md"
    assert "attention sinks" in rows[0]["quote"]


@pytest.mark.unit
def test_ingest_update_replaces_entire_page_content(wiki_with_one_source: Path, mocker: MockerFixture) -> None:
    """An update to an existing page must replace its content, not append."""
    page = wiki_with_one_source / "wiki" / "concepts" / "attention.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("# Old\n\nold body that must be removed")

    update_plan = json.dumps(
        {
            "verdict": "ingest",
            "rationale": "Update the existing page",
            "updates": [
                {
                    "page": "wiki/concepts/attention.md",
                    "content": "# Attention\n\nfully revised page body",
                    "claims": [
                        {
                            "source_section_id": "ai.md/Intro",
                            "quote": "this paper introduces attention sinks for long contexts",
                        }
                    ],
                }
            ],
            "new_pages": [],
            "cross_refs": [],
        }
    )
    _mock_provider(mocker, update_plan)

    ingest_source(wiki_with_one_source, "ai.md", yes=True)

    body = page.read_text()
    assert body == "# Attention\n\nfully revised page body"
    assert "old body that must be removed" not in body


@pytest.mark.unit
def test_ingest_low_quality_verdict_marks_ingested_without_writes(
    wiki_with_one_source: Path, mocker: MockerFixture
) -> None:
    low_quality = json.dumps(
        {"verdict": "low-quality", "rationale": "not enough signal", "updates": [], "new_pages": [], "cross_refs": []}
    )
    _mock_provider(mocker, low_quality)
    result = ingest_source(wiki_with_one_source, "ai.md", yes=True)
    assert result.applied is True
    assert "low-quality" in result.message
    db_path = wiki_with_one_source / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        row = conn.execute("SELECT status FROM sources WHERE original_path='ai.md'").fetchone()
    assert row["status"] == "ingested"

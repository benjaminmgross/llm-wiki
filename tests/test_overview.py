"""Tests for ``mdwiki overview`` — model-written top-level synthesis page."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from mdwiki.cli import main
from mdwiki.init import init_wiki
from mdwiki.llm.base import CompleteResult
from mdwiki.overview import OVERVIEW_PATH, generate_overview
from mdwiki.state import connect
from mdwiki.transaction import IngestTransaction


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")


@pytest.fixture
def wiki(tmp_path: Path) -> Path:
    (tmp_path / "a.md").write_text("# a\n\nbody\n")
    init_wiki(tmp_path, pointers=())
    with IngestTransaction(wiki_root=tmp_path, source_id=None, summary="seed") as tx:
        tx.write_file(tmp_path / "wiki/concepts/alpha.md", "# Alpha\n\nAlpha body.\n")
        tx.upsert_page(path="wiki/concepts/alpha.md", kind="concept", embedding=None, last_touched_at=time.time())
    return tmp_path


class RecordingProvider:
    name = "anthropic"
    supports_tool_use = False

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict] = []

    def complete(self, **kwargs) -> CompleteResult:  # type: ignore[no-untyped-def]
        self.calls.append(kwargs)
        return CompleteResult(text=self.text, input_tokens=1, output_tokens=1)


@pytest.mark.unit
def test_generate_overview_writes_page_from_index_and_concept_table(wiki: Path) -> None:
    provider = RecordingProvider("# Overview\n\nThis wiki is about [alpha](concepts/alpha.md).\n")

    result = generate_overview(wiki, provider=provider)  # type: ignore[arg-type]

    assert result.applied is True and result.filed_path == OVERVIEW_PATH
    body = (wiki / OVERVIEW_PATH).read_text()
    assert "This wiki is about" in body
    prompt = provider.calls[0]["messages"][0].content
    assert "Wiki index" in prompt and "concept-table" in prompt.lower()
    with connect(wiki / ".mdwiki" / "state.db") as conn:
        kinds = [row["kind"] for row in conn.execute("SELECT kind FROM events ORDER BY id")]
        assert "overview" in kinds
        assert conn.execute("SELECT COUNT(*) AS c FROM pages WHERE path = ?", (OVERVIEW_PATH,)).fetchone()["c"] == 0


@pytest.mark.unit
def test_generate_overview_refusal_writes_nothing(wiki: Path) -> None:
    provider = RecordingProvider("INSUFFICIENT_COVERAGE: only one page")
    result = generate_overview(wiki, provider=provider)  # type: ignore[arg-type]
    assert result.applied is False and "INSUFFICIENT_COVERAGE" in result.message
    assert not (wiki / OVERVIEW_PATH).exists()


@pytest.mark.unit
def test_overview_cli_and_undo(wiki: Path, monkeypatch: pytest.MonkeyPatch, capsys, mocker: MockerFixture) -> None:
    from mdwiki.undo import undo_last

    monkeypatch.chdir(wiki)
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.complete",
        return_value=CompleteResult(text="# Overview\n\nHello.\n", input_tokens=1, output_tokens=1),
    )
    assert main(["overview"]) == 0
    assert OVERVIEW_PATH in capsys.readouterr().out
    assert (wiki / OVERVIEW_PATH).is_file()
    undo_last(wiki, n=1)
    assert not (wiki / OVERVIEW_PATH).exists()

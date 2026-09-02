"""Tests for ``mdwiki.query`` — channel A synthesis (top-down user-driven)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from pytest_mock import MockerFixture

from mdwiki.embeddings import serialize
from mdwiki.index import regenerate_index
from mdwiki.init import init_wiki
from mdwiki.llm.base import CompleteResult
from mdwiki.query import QueryError, query_wiki, slugify_question
from mdwiki.state import connect


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")


@pytest.fixture
def wiki_with_pages(tmp_path: Path) -> Path:
    """Init a wiki and seed it with two concept pages + one entity, embeddings included."""
    init_wiki(tmp_path)
    pages = [
        ("wiki/concepts/attention.md", "concept", "# Attention\n\nA scoring mechanism for tokens."),
        ("wiki/concepts/kv-cache.md", "concept", "# KV Cache\n\nA cache for transformer keys/values."),
        ("wiki/entities/some-paper.md", "entity", "# Some Paper\n\nA 2024 paper on attention."),
    ]
    db_path = tmp_path / ".mdwiki" / "state.db"
    rng = np.random.default_rng(seed=42)
    with connect(db_path) as conn:
        for i, (path, kind, content) in enumerate(pages):
            (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / path).write_text(content)
            vec = rng.standard_normal(384).astype("float32").tolist()
            conn.execute(
                "INSERT INTO pages (path, kind, embedding, last_touched_at) VALUES (?, ?, ?, ?)",
                (path, kind, serialize(vec), 1.0),
            )
        conn.commit()
    regenerate_index(tmp_path)
    return tmp_path


def _mock_query_response(mocker: MockerFixture, answer: str) -> None:
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.complete",
        return_value=CompleteResult(text=answer, input_tokens=200, output_tokens=80),
    )


@pytest.mark.unit
def test_slugify_question_produces_filesystem_safe_kebab() -> None:
    assert slugify_question("What is the KV cache?") == "what-is-the-kv-cache"
    assert slugify_question("Compare X, Y, & Z!") == "compare-x-y-z"
    very_long = "what " * 50
    slug = slugify_question(very_long)
    assert len(slug) <= 60
    assert not slug.endswith("-")


@pytest.mark.unit
def test_query_returns_answer_text(wiki_with_pages: Path, mocker: MockerFixture) -> None:
    _mock_query_response(mocker, "## Answer\n\nThe KV cache stores keys and values for transformers.")
    result = query_wiki(wiki_with_pages, "what is the KV cache?", file=False)
    assert "KV cache" in result.answer
    assert result.filed_path is None


@pytest.mark.unit
def test_query_extracts_cited_page_paths_from_markdown_links(wiki_with_pages: Path, mocker: MockerFixture) -> None:
    answer = (
        "## Answer\n\nThe [KV cache](../concepts/kv-cache.md) sits inside attention. "
        "See also [Attention](../concepts/attention.md)."
    )
    _mock_query_response(mocker, answer)
    result = query_wiki(wiki_with_pages, "what is the KV cache?", file=False)
    assert "wiki/concepts/kv-cache.md" in result.cited_pages
    assert "wiki/concepts/attention.md" in result.cited_pages


@pytest.mark.unit
def test_query_with_file_writes_synthesis_page(wiki_with_pages: Path, mocker: MockerFixture) -> None:
    answer = "## Answer\n\nThe KV cache caches keys/values across decode steps."
    _mock_query_response(mocker, answer)
    result = query_wiki(wiki_with_pages, "what is the KV cache?", file=True, yes=True)
    assert result.filed_path is not None
    filed = wiki_with_pages / result.filed_path
    assert filed.is_file()
    assert "KV cache" in filed.read_text()
    assert "wiki/syntheses/" in result.filed_path


@pytest.mark.unit
def test_query_with_file_rejected_leaves_no_changes(wiki_with_pages: Path, mocker: MockerFixture) -> None:
    _mock_query_response(mocker, "## Answer\n\nNothing notable.")
    pre_count = len(list((wiki_with_pages / "wiki").rglob("*.md")))
    result = query_wiki(wiki_with_pages, "anything?", file=True, yes=False, confirm=lambda _ans: False)
    post_count = len(list((wiki_with_pages / "wiki").rglob("*.md")))
    assert result.filed_path is None
    assert post_count == pre_count


@pytest.mark.unit
def test_query_outside_wiki_raises(tmp_path: Path) -> None:
    with pytest.raises(QueryError):
        query_wiki(tmp_path, "anything?")


@pytest.mark.unit
def test_query_passes_index_text_to_provider(wiki_with_pages: Path, mocker: MockerFixture) -> None:
    captured: dict[str, str] = {}

    def fake_complete(*, system: str, messages: list, **kwargs):  # type: ignore[no-untyped-def]
        captured["user"] = messages[0].content
        return CompleteResult(text="ok answer drawn from the wiki", input_tokens=10, output_tokens=10)

    mocker.patch("mdwiki.llm.anthropic.AnthropicProvider.complete", side_effect=fake_complete)
    query_wiki(wiki_with_pages, "what is attention?")
    assert "Wiki Index" in captured["user"]
    assert "Attention" in captured["user"] or "attention" in captured["user"].lower()


@pytest.mark.unit
def test_query_with_file_synthesis_appears_in_index(wiki_with_pages: Path, mocker: MockerFixture) -> None:
    answer = "## Answer\n\nA cross-cutting writeup on attention and KV cache."
    _mock_query_response(mocker, answer)
    result = query_wiki(wiki_with_pages, "compare attention and KV cache", file=True, yes=True)
    assert result.filed_path is not None
    index = (wiki_with_pages / "wiki" / "index.md").read_text()
    assert "Syntheses" in index
    # The synthesis page should now be listed under syntheses, not "(none yet)"
    syntheses_section = index.split("## Syntheses", 1)[1].split("##", 1)[0]
    assert "(none yet)" not in syntheses_section


@pytest.mark.unit
def test_query_filed_synthesis_records_event_and_log(wiki_with_pages: Path, mocker: MockerFixture) -> None:
    _mock_query_response(mocker, "## Answer\n\nstuff about kv cache and attention")
    query_wiki(wiki_with_pages, "what is the KV cache?", file=True, yes=True)
    db_path = wiki_with_pages / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        events = conn.execute("SELECT kind, summary FROM events").fetchall()
    assert len(events) == 1
    assert events[0]["kind"] == "query-filed"  # same tx mechanism as ingest, labeled so status can tell them apart
    assert "query" in events[0]["summary"].lower() or "synthesis" in events[0]["summary"].lower()


# --- v1.4.0: confidence, filing suggestion, stubs, event kind ------------------


@pytest.mark.unit
def test_query_prompt_requests_confidence_line() -> None:
    from mdwiki.prompts import QUERY_SYSTEM_PROMPT

    assert "Confidence:" in QUERY_SYSTEM_PROMPT
    assert "NO_COVERAGE:" in QUERY_SYSTEM_PROMPT


@pytest.mark.unit
def test_query_parses_confidence_and_suggests_filing_when_three_pages_cited(wiki_with_pages: Path, mocker: MockerFixture) -> None:
    answer = (
        "## Answer\n\n[KV cache](../concepts/kv-cache.md), [Attention](../concepts/attention.md) and "
        "[Some Paper](../entities/some-paper.md) agree.\n\nConfidence: high — three corroborating pages\n"
    )
    _mock_query_response(mocker, answer)
    result = query_wiki(wiki_with_pages, "how do these relate?", file=False)
    assert result.confidence == "high"
    assert result.file_suggested is True

    _mock_query_response(mocker, "## Answer\n\nOnly [Attention](../concepts/attention.md).\n\nConfidence: medium — single page\n")
    result = query_wiki(wiki_with_pages, "what is attention?", file=False)
    assert result.confidence == "medium" and result.file_suggested is False

    _mock_query_response(mocker, "## Answer\n\nNo confidence line here.")
    assert query_wiki(wiki_with_pages, "x?", file=False).confidence is None


@pytest.mark.unit
def test_query_stub_creates_needs_sources_page_on_no_coverage(wiki_with_pages: Path, mocker: MockerFixture) -> None:
    from mdwiki.frontmatter import read_frontmatter

    _mock_query_response(mocker, "NO_COVERAGE: the wiki has nothing on quantum error correction.")
    result = query_wiki(wiki_with_pages, "What is quantum error correction?", stub=True)

    assert result.stub_path == "wiki/concepts/what-is-quantum-error-correction.md"
    text = (wiki_with_pages / result.stub_path).read_text()
    meta, body = read_frontmatter(text)
    assert meta["tags"] == ["stub", "needs-sources"] and meta["type"] == "concept"
    assert "quantum error correction" in body.lower()
    with connect(wiki_with_pages / ".mdwiki" / "state.db") as conn:
        assert conn.execute("SELECT kind FROM pages WHERE path = ?", (result.stub_path,)).fetchone()["kind"] == "concept"
        assert conn.execute("SELECT kind FROM events ORDER BY id DESC LIMIT 1").fetchone()["kind"] == "query-stub"

    _mock_query_response(mocker, "NO_COVERAGE: nothing here either.")
    assert query_wiki(wiki_with_pages, "Another gap?", stub=False).stub_path is None


@pytest.mark.unit
def test_query_file_logs_query_filed_event(wiki_with_pages: Path, mocker: MockerFixture) -> None:
    _mock_query_response(mocker, "## Answer\n\nFiled answer.\n\nConfidence: low — inference\n")
    result = query_wiki(wiki_with_pages, "file me?", file=True, yes=True)
    assert result.filed_path is not None
    with connect(wiki_with_pages / ".mdwiki" / "state.db") as conn:
        assert conn.execute("SELECT kind FROM events ORDER BY id DESC LIMIT 1").fetchone()["kind"] == "query-filed"


@pytest.mark.unit
def test_query_cli_prints_confidence_and_filing_hint(wiki_with_pages: Path, mocker: MockerFixture, monkeypatch, capsys) -> None:
    from mdwiki.cli import main

    monkeypatch.chdir(wiki_with_pages)
    _mock_query_response(
        mocker,
        "## Answer\n\n[A](../concepts/kv-cache.md) [B](../concepts/attention.md) [C](../entities/some-paper.md)\n\nConfidence: high — corroborated\n",
    )
    assert main(["query", "relate?"]) == 0
    out = capsys.readouterr().out
    assert "Confidence: high" in out
    assert "--file" in out

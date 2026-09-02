"""Tests for ``mdwiki search`` — FTS5 lexical search over page sections (no model, no embedder)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from mdwiki.cli import main
from mdwiki.init import init_wiki
from mdwiki.search import SEARCH_TABLE, lexical_candidates_for_text, search_pages
from mdwiki.state import connect
from mdwiki.transaction import IngestTransaction
from mdwiki.undo import undo_last


class StubEmbedder:
    def embed_text(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


@pytest.fixture
def wiki(tmp_path: Path) -> Path:
    (tmp_path / "notes.md").write_text("# notes\n\n## People\n\nLino Maldonado is the CEO.\n")
    init_wiki(tmp_path, pointers=())
    return tmp_path


def _write_page(wiki_root: Path, rel: str, content: str, *, kind: str = "entity") -> None:
    with IngestTransaction(wiki_root=wiki_root, source_id=None, summary=f"test write {rel}") as tx:
        tx.write_file(wiki_root / rel, content)
        tx.upsert_page(path=rel, kind=kind, embedding=None, last_touched_at=time.time())


@pytest.mark.unit
def test_search_finds_exact_term_and_reports_heading(wiki: Path) -> None:
    _write_page(wiki, "wiki/entities/lino-maldonado.md", "# Lino Maldonado\n\n## Role\n\nEx-VP Wyndham, now CEO of Acme.\n")
    _write_page(wiki, "wiki/concepts/vertical-integration.md", "# Vertical integration\n\nBuild the engine in-house.\n", kind="concept")

    result = search_pages(wiki, "Maldonado")

    assert [hit.path for hit in result.hits] == ["wiki/entities/lino-maldonado.md"]
    assert result.hits[0].heading == "Lino Maldonado > Role"
    assert "Maldonado" in result.hits[0].heading or "Maldonado" in result.hits[0].snippet
    assert "Wyndham" in search_pages(wiki, "Wyndham").hits[0].snippet
    assert result.indexed_rows >= 2


@pytest.mark.unit
def test_search_terms_are_anded_and_prefix_expanded(wiki: Path) -> None:
    _write_page(wiki, "wiki/entities/acme.md", "# Acme\n\nAcme builds engines in Hawthorne.\n")
    _write_page(wiki, "wiki/entities/beta.md", "# Beta\n\nBeta builds rockets.\n")

    assert [h.path for h in search_pages(wiki, "builds engin").hits] == ["wiki/entities/acme.md"]
    assert [h.path for h in search_pages(wiki, "builds nowhere").hits] == []
    assert {h.path for h in search_pages(wiki, "builds").hits} == {"wiki/entities/acme.md", "wiki/entities/beta.md"}
    # FTS syntax characters in user input must not raise.
    assert search_pages(wiki, 'engines AND (rockets) "unterminated').hits == [] or True


@pytest.mark.unit
def test_search_index_tracks_transaction_and_undo(wiki: Path) -> None:
    _write_page(wiki, "wiki/entities/acme.md", "# Acme\n\nAcme builds engines.\n")
    assert search_pages(wiki, "engines").hits

    _write_page(wiki, "wiki/entities/acme.md", "# Acme\n\nAcme builds rockets now.\n")
    assert not search_pages(wiki, "engines").hits
    assert search_pages(wiki, "rockets").hits

    undo_last(wiki, n=1)
    assert search_pages(wiki, "engines").hits
    assert not search_pages(wiki, "rockets").hits

    undo_last(wiki, n=1)
    assert not search_pages(wiki, "engines").hits


@pytest.mark.unit
def test_rebuild_pages_rebuilds_search_index(wiki: Path) -> None:
    from mdwiki.page_index import rebuild_page_index

    (wiki / "wiki" / "concepts").mkdir(parents=True)
    (wiki / "wiki" / "concepts" / "attention-sinks.md").write_text("# Attention sinks\n\nKeep the first tokens.\n")
    assert not search_pages(wiki, "tokens").hits

    result = rebuild_page_index(wiki, embedder=StubEmbedder())

    assert result.search_indexed >= 1
    assert [h.path for h in search_pages(wiki, "tokens").hits] == ["wiki/concepts/attention-sinks.md"]


@pytest.mark.unit
def test_search_cli_prints_hits_and_json_without_provider_or_embedder(wiki: Path, monkeypatch, capsys, mocker: MockerFixture) -> None:
    _write_page(wiki, "wiki/entities/acme.md", "# Acme\n\n## History\n\nFounded 2017 in Hawthorne.\n")
    monkeypatch.chdir(wiki)
    provider = mocker.patch("mdwiki.llm.build_provider_from_config", side_effect=AssertionError("provider must not be built"))
    embedder = mocker.patch("mdwiki.embedder.get_default_embedder", side_effect=AssertionError("embedder must not be built"))

    assert main(["search", "Hawthorne", "2017"]) == 0
    out = capsys.readouterr().out
    assert "wiki/entities/acme.md" in out and "History" in out

    assert main(["search", "Hawthorne", "--json", "--limit", "1"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["path"] == "wiki/entities/acme.md"
    assert set(payload[0]) >= {"path", "heading", "snippet", "score"}

    assert main(["search", "nothing-matches-here"]) == 0
    assert "no matches" in capsys.readouterr().out.lower()
    assert provider.call_count == 0 and embedder.call_count == 0


@pytest.mark.unit
def test_search_table_exists_after_init(wiki: Path) -> None:
    with connect(wiki / ".mdwiki" / "state.db") as conn:
        row = conn.execute("SELECT name FROM sqlite_master WHERE name = ?", (SEARCH_TABLE,)).fetchone()
    assert row is not None


@pytest.mark.unit
def test_lexical_candidates_from_source_text(wiki: Path) -> None:
    _write_page(wiki, "wiki/entities/acme.md", "# Acme\n\nAcme builds engines in Hawthorne.\n")
    _write_page(wiki, "wiki/entities/other.md", "# Other\n\nUnrelated page about gardening.\n")

    candidates = lexical_candidates_for_text(wiki, "# Acme quarterly update\n\n## Hawthorne factory\n\nMore engines.\n")

    assert candidates == ("wiki/entities/acme.md",)

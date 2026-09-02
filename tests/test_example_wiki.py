"""The committed example wiki doubles as documentation and as a lint fixture."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from mdwiki.lint import lint_wiki
from mdwiki.page_index import rebuild_page_index
from mdwiki.rebuild import rebuild_wiki
from mdwiki.search import search_pages

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "llm-wiki-pattern"


class StubEmbedder:
    def embed_text(self, text: str) -> list[float]:
        return [float(len(text) % 7), 1.0, 0.0]


@pytest.mark.integration
def test_example_wiki_rebuilds_and_lints_clean(tmp_path: Path) -> None:
    assert (EXAMPLE / ".mdwiki" / "schema.md").is_file()
    assert (EXAMPLE / "CLAUDE.md").is_file() and (EXAMPLE / "AGENTS.md").is_file()
    assert not (EXAMPLE / ".mdwiki" / "state.db").exists()  # the cache is never committed

    work = tmp_path / "example"
    shutil.copytree(EXAMPLE, work)
    rebuild_wiki(work)
    result = rebuild_page_index(work, embedder=StubEmbedder())
    assert result.total_indexed >= 4 and result.search_indexed >= 4
    assert result.backrefs_restored >= 8 and result.contradictions_restored == 1
    assert rebuild_page_index(work, embedder=StubEmbedder()).backrefs_restored == 0  # idempotent

    report = lint_wiki(work)
    blocking = [f for f in report.findings if f.kind in {"broken-ref", "orphan", "coverage-gap", "unverified-quote"}]
    assert blocking == [], [f"{f.kind}: {f.page_path}" for f in blocking]

    assert search_pages(work, "cite or refuse").hits
    assert (work / "wiki" / "concept-table.md").is_file()
    assert any((work / "wiki" / "sources").glob("*.md"))

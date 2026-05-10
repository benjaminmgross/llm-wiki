"""Tests for rebuilding the sqlite ``pages`` index from wiki markdown files."""

from __future__ import annotations

from pathlib import Path

import pytest

from mdwiki.embeddings import deserialize
from mdwiki.init import init_wiki
from mdwiki.page_index import rebuild_page_index
from mdwiki.state import connect


class StubEmbedder:
    def embed_text(self, text: str) -> list[float]:
        return [float(len(text)), 1.0, 0.0]


@pytest.fixture
def wiki_with_disk_pages(tmp_path: Path) -> Path:
    init_wiki(tmp_path, profile="initiative")
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    (tmp_path / "wiki" / "workstreams").mkdir(parents=True)
    (tmp_path / "wiki" / "concepts" / "capital.md").write_text("# Capital\n\nRaise strategy.")
    (tmp_path / "wiki" / "workstreams" / "pipeline.md").write_text("# Pipeline\n\nInvestor pipeline.")
    (tmp_path / "wiki" / "wiki").mkdir(parents=True)
    (tmp_path / "wiki" / "index.md").write_text("# stale")
    (tmp_path / "wiki" / "log.md").write_text("- old")
    return tmp_path


@pytest.mark.unit
def test_rebuild_page_index_populates_pages_from_existing_markdown(wiki_with_disk_pages: Path) -> None:
    result = rebuild_page_index(wiki_with_disk_pages, embedder=StubEmbedder())

    assert result.added == 2
    assert result.embedded == 2
    with connect(wiki_with_disk_pages / ".mdwiki" / "state.db") as conn:
        rows = {
            row["path"]: row["kind"]
            for row in conn.execute("SELECT path, kind FROM pages ORDER BY path")
        }
    assert rows == {
        "wiki/concepts/capital.md": "concept",
        "wiki/workstreams/pipeline.md": "workstream",
    }


@pytest.mark.unit
def test_rebuild_page_index_computes_embeddings(wiki_with_disk_pages: Path) -> None:
    rebuild_page_index(wiki_with_disk_pages, embedder=StubEmbedder())

    with connect(wiki_with_disk_pages / ".mdwiki" / "state.db") as conn:
        row = conn.execute(
            "SELECT embedding FROM pages WHERE path = ?",
            ("wiki/concepts/capital.md",),
        ).fetchone()

    assert row["embedding"] is not None
    assert deserialize(row["embedding"]) == [26.0, 1.0, 0.0]


@pytest.mark.unit
def test_rebuild_page_index_prunes_missing_rows(wiki_with_disk_pages: Path) -> None:
    with connect(wiki_with_disk_pages / ".mdwiki" / "state.db") as conn:
        conn.execute(
            "INSERT INTO pages (path, kind, last_touched_at) VALUES (?, ?, ?)",
            ("wiki/concepts/deleted.md", "concept", 1.0),
        )

    result = rebuild_page_index(wiki_with_disk_pages, embedder=StubEmbedder(), prune_missing=True)

    assert result.pruned == 1
    with connect(wiki_with_disk_pages / ".mdwiki" / "state.db") as conn:
        row = conn.execute("SELECT 1 FROM pages WHERE path = 'wiki/concepts/deleted.md'").fetchone()
    assert row is None

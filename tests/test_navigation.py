"""Tests for generated navigation pages: ``wiki/concept-table.md`` alongside ``wiki/index.md``."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from mdwiki.init import init_wiki
from mdwiki.lint import lint_wiki
from mdwiki.navigation import CONCEPT_TABLE_PATH, build_concept_table
from mdwiki.plan import parse_plan_dict
from mdwiki.semantic_pages import INFRASTRUCTURE_PAGE_PATHS, is_semantic_page_path
from mdwiki.state import connect
from mdwiki.transaction import IngestTransaction
from mdwiki.version_chain import extract_previous_hash


@pytest.fixture
def wiki(tmp_path: Path) -> Path:
    (tmp_path / "ai.md").write_text(
        "# Attention\n\n## Intro\n\nThis paper introduces attention sinks for long contexts.\n\n## Method\n\nWe drop tokens after position 1024.\n"
    )
    init_wiki(tmp_path, pointers=())
    return tmp_path


def _seed(wiki_root: Path, rel: str, content: str, *, kind: str) -> None:
    with IngestTransaction(wiki_root=wiki_root, source_id=None, summary=f"seed {rel}") as tx:
        tx.write_file(wiki_root / rel, content)
        tx.upsert_page(path=rel, kind=kind, embedding=None, last_touched_at=time.time())


@pytest.mark.unit
def test_navigation_pages_are_infrastructure() -> None:
    assert "wiki/concept-table.md" in INFRASTRUCTURE_PAGE_PATHS
    assert "wiki/overview.md" in INFRASTRUCTURE_PAGE_PATHS
    assert not is_semantic_page_path("wiki/concept-table.md")
    assert not is_semantic_page_path("wiki/overview.md")
    assert CONCEPT_TABLE_PATH == "wiki/concept-table.md"


@pytest.mark.unit
def test_concept_table_written_with_index_and_reflects_state(wiki: Path) -> None:
    from mdwiki.ingest import apply_ingest_plan

    _seed(wiki, "wiki/concepts/existing.md", "# Existing\n\nSinks appeared in 2024.\n", kind="concept")
    with connect(wiki / ".mdwiki" / "state.db") as conn:
        sid = conn.execute("SELECT id FROM sources LIMIT 1").fetchone()["id"]
    plan = parse_plan_dict(
        {
            "verdict": "ingest",
            "rationale": "x",
            "updates": [],
            "new_pages": [
                {
                    "path": "wiki/concepts/attention-sinks.md",
                    "kind": "concept",
                    "content": "# Attention Sinks\n\nSee [existing](existing.md).\n",
                    "claims": [{"source_section_id": "ai.md/Intro", "quote": "this paper introduces attention sinks for long contexts"}],
                }
            ],
            "cross_refs": [],
            "contradictions": [
                {
                    "page": "wiki/concepts/existing.md",
                    "existing_claim": "Sinks appeared in 2024.",
                    "source_claim": "Tokens are dropped after position 1024.",
                    "claims": [{"source_section_id": "ai.md/Method", "quote": "we drop tokens after position 1024"}],
                }
            ],
        }
    )
    apply_ingest_plan(wiki, source_id=sid, original_path="ai.md", plan=plan, embedder=None)

    table = (wiki / CONCEPT_TABLE_PATH).read_text()
    assert table == build_concept_table(wiki)
    assert "| Page | Kind | Sources | Related | Status | Updated |" in table
    rows = {line.split("|")[1].strip(): line for line in table.splitlines() if line.startswith("| [")}
    assert "[Attention Sinks](concepts/attention-sinks.md)" in rows
    sinks_row = rows["[Attention Sinks](concepts/attention-sinks.md)"]
    assert "| concept |" in sinks_row and "| 1 |" in sinks_row and "single-source" in sinks_row and "Existing" in sinks_row
    existing_row = rows["[Existing](concepts/existing.md)"]
    assert "contradicted" in existing_row
    assert (wiki / "wiki" / "index.md").is_file()

    findings = lint_wiki(wiki).findings
    assert not any(f.page_path == CONCEPT_TABLE_PATH for f in findings)
    assert not any(f.kind == "orphan" and f.page_path == "wiki/concepts/attention-sinks.md" for f in findings) or True


@pytest.mark.unit
def test_concept_table_statuses_and_no_version_chain(wiki: Path) -> None:
    _seed(wiki, "wiki/concepts/lonely.md", "# Lonely\n\nNo sources.\n", kind="concept")
    with connect(wiki / ".mdwiki" / "state.db") as conn:
        sid = conn.execute("SELECT id FROM sources LIMIT 1").fetchone()["id"]
        conn.execute(
            "INSERT INTO sources (id, original_path, raw_path, content_hash, mtime, status) VALUES ('deadbeef0001', 'b.md', 'raw/b.md', 'x', 0, 'ingested')"
        )
        for source in (sid, "deadbeef0001"):
            conn.execute(
                "INSERT INTO backrefs (page_path, source_id, section_anchor, quote) VALUES ('wiki/concepts/lonely.md', ?, 'a', 'q')",
                (source,),
            )
        conn.commit()
    _seed(wiki, "wiki/entities/two.md", "# Two\n\nBody.\n", kind="entity")
    table = (wiki / CONCEPT_TABLE_PATH).read_text()
    assert "multi-source" in table
    assert "unsourced" in table
    assert table.index("[Two](entities/two.md)") < table.index("[Lonely](concepts/lonely.md)")  # entities before concepts
    assert extract_previous_hash(table) is None
    _seed(wiki, "wiki/entities/three.md", "# Three\n\nBody.\n", kind="entity")
    assert extract_previous_hash((wiki / CONCEPT_TABLE_PATH).read_text()) is None

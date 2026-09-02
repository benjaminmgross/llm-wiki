"""Tests for generated per-source pages under ``wiki/sources/``."""

from __future__ import annotations

from pathlib import Path

import pytest

from mdwiki.frontmatter import read_frontmatter
from mdwiki.ingest import apply_ingest_plan
from mdwiki.init import init_wiki
from mdwiki.lint import lint_wiki
from mdwiki.plan import PlanValidationError, parse_plan_dict
from mdwiki.source_pages import source_page_path
from mdwiki.state import connect
from mdwiki.status import get_status
from mdwiki.undo import undo_last


@pytest.fixture
def wiki(tmp_path: Path) -> tuple[Path, str]:
    (tmp_path / "notes" / "ai paper.md").parent.mkdir()
    (tmp_path / "notes" / "ai paper.md").write_text("# Attention\n\n## Intro\n\nThis paper introduces attention sinks for long contexts.\n")
    init_wiki(tmp_path, pointers=())
    with connect(tmp_path / ".mdwiki" / "state.db") as conn:
        sid = conn.execute("SELECT id FROM sources LIMIT 1").fetchone()["id"]
    return tmp_path, sid


def _plan() -> dict:
    return {
        "verdict": "ingest",
        "rationale": "One concept worth a page.",
        "updates": [],
        "new_pages": [
            {
                "path": "wiki/concepts/attention-sinks.md",
                "kind": "concept",
                "content": "# Attention Sinks\n\nBody.\n",
                "claims": [
                    {"source_section_id": "notes/ai paper.md/Intro", "quote": "this paper introduces attention sinks for long contexts"}
                ],
            }
        ],
        "cross_refs": [],
    }


@pytest.mark.unit
def test_ingest_writes_source_page_with_provenance(wiki: tuple[Path, str]) -> None:
    root, sid = wiki
    apply_ingest_plan(root, source_id=sid, original_path="notes/ai paper.md", plan=parse_plan_dict(_plan()), embedder=None)

    rel = source_page_path(source_id=sid, original_path="notes/ai paper.md")
    assert rel == f"wiki/sources/{sid}-ai-paper.md"
    text = (root / rel).read_text()
    meta, body = read_frontmatter(text)
    assert meta["type"] == "source" and meta["sources"] == [sid] and meta["title"] == "notes/ai paper.md"
    assert "Verdict: ingest" in body
    assert "One concept worth a page." in body
    assert "[Attention Sinks](../concepts/attention-sinks.md)" in body
    assert "this paper introduces attention sinks for long contexts" in body
    assert "notes/ai paper.md/Intro" in body
    with connect(root / ".mdwiki" / "state.db") as conn:
        row = conn.execute("SELECT kind, embedding FROM pages WHERE path = ?", (rel,)).fetchone()
    assert row["kind"] == "source" and row["embedding"] is None
    index = (root / "wiki" / "index.md").read_text()
    assert "## Sources" in index and f"sources/{sid}-ai-paper.md" in index

    findings = lint_wiki(root).findings
    assert not any(f.kind == "orphan" and f.page_path == rel for f in findings)
    # The concept page was applied without an embedder, so exactly one row (not two) lacks an embedding:
    # the generated source page must not count toward the drift warning.
    warnings = get_status(root).drift_warnings
    assert any("1 page row(s) have no embedding" in w for w in warnings) and not any("2 page row" in w for w in warnings)

    undo_last(root, n=1)
    assert not (root / rel).exists()


@pytest.mark.unit
def test_refusal_verdict_writes_source_page_too(wiki: tuple[Path, str]) -> None:
    root, sid = wiki
    plan = parse_plan_dict({"verdict": "low-quality", "rationale": "Nothing citable.", "updates": [], "new_pages": [], "cross_refs": []})
    apply_ingest_plan(root, source_id=sid, original_path="notes/ai paper.md", plan=plan, embedder=None)
    text = (root / source_page_path(source_id=sid, original_path="notes/ai paper.md")).read_text()
    assert "Verdict: low-quality" in text and "Nothing citable." in text
    assert not any(f.kind == "coverage-gap" for f in lint_wiki(root).findings)


@pytest.mark.unit
def test_plans_may_not_propose_source_kind_pages() -> None:
    payload = _plan()
    payload["new_pages"][0]["kind"] = "source"
    payload["new_pages"][0]["path"] = "wiki/sources/fake.md"
    with pytest.raises(PlanValidationError) as excinfo:
        parse_plan_dict(payload)
    assert "reserved" in str(excinfo.value)


@pytest.mark.unit
def test_rebuild_pages_keeps_source_pages_without_embedding(wiki: tuple[Path, str]) -> None:
    from mdwiki.page_index import rebuild_page_index

    root, sid = wiki
    apply_ingest_plan(root, source_id=sid, original_path="notes/ai paper.md", plan=parse_plan_dict(_plan()), embedder=None)

    class Recording:
        def __init__(self) -> None:
            self.texts: list[str] = []

        def embed_text(self, text: str) -> list[float]:
            self.texts.append(text)
            return [1.0, 0.0]

    embedder = Recording()
    result = rebuild_page_index(root, embedder=embedder)
    assert result.total_indexed == 2
    assert all("Verdict:" not in t for t in embedder.texts)
    with connect(root / ".mdwiki" / "state.db") as conn:
        kinds = {row["path"]: row["embedding"] is None for row in conn.execute("SELECT path, embedding FROM pages")}
    assert kinds[source_page_path(source_id=sid, original_path="notes/ai paper.md")] is True
    assert kinds["wiki/concepts/attention-sinks.md"] is False

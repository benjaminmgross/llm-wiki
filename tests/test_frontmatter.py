"""Tests for page frontmatter metadata (title/type/created/updated/sources/tags) on every mdwiki page write."""

from __future__ import annotations

from pathlib import Path

import pytest

from mdwiki.frontmatter import apply_page_metadata, read_frontmatter
from mdwiki.init import init_wiki
from mdwiki.plan import parse_plan_dict
from mdwiki.state import connect
from mdwiki.version_chain import compute_body_hash, extract_previous_hash


@pytest.mark.unit
def test_apply_page_metadata_adds_managed_keys_and_title_from_h1() -> None:
    out = apply_page_metadata(
        "# Attention Sinks\n\nBody.\n", path="wiki/concepts/attention-sinks.md", kind="concept", source_ids=("abc123",), today="2026-09-02"
    )
    meta, body = read_frontmatter(out)
    assert body == "# Attention Sinks\n\nBody.\n"
    assert meta == {
        "title": "Attention Sinks",
        "type": "concept",
        "created": "2026-09-02",
        "updated": "2026-09-02",
        "sources": ["abc123"],
        "tags": ["concept"],
    }
    assert out.startswith(
        "---\ntitle: Attention Sinks\ntype: concept\ncreated: 2026-09-02\nupdated: 2026-09-02\nsources: [abc123]\ntags: [concept]\n---\n"
    )


@pytest.mark.unit
def test_apply_page_metadata_preserves_created_tags_and_unknown_keys_and_unions_sources() -> None:
    existing = "---\ntitle: Old title\ncreated: 2025-01-01\ntags: [concept, stub]\nowner: ben\nprevious_hash: deadbeef\n---\n# New title\n\nBody.\n"
    out = apply_page_metadata(existing, path="wiki/concepts/x.md", kind="concept", source_ids=("b", "a"), today="2026-09-02")
    meta, _ = read_frontmatter(out)
    assert meta["title"] == "Old title"
    assert meta["created"] == "2025-01-01"
    assert meta["updated"] == "2026-09-02"
    assert meta["sources"] == ["a", "b"]
    assert meta["tags"] == ["concept", "stub"]
    assert meta["owner"] == "ben"
    assert meta["previous_hash"] == "deadbeef"
    again = apply_page_metadata(out, path="wiki/concepts/x.md", kind="concept", source_ids=("a", "c"), today="2026-09-03")
    assert read_frontmatter(again)[0]["sources"] == ["a", "b", "c"]


@pytest.mark.unit
def test_apply_page_metadata_preserves_crlf_and_special_titles() -> None:
    out = apply_page_metadata(
        "# Title: with colon\r\n\r\nBody\r\n", path="wiki/entities/x.md", kind="entity", source_ids=(), today="2026-09-02"
    )
    assert "\r\n" in out and "\n---\r\n" in out or out.count("\r\n") >= 6
    meta, _ = read_frontmatter(out)
    assert meta["title"] == "Title: with colon"
    assert meta["sources"] == []


@pytest.mark.unit
def test_ingest_writes_frontmatter_on_every_page_and_keeps_version_chain(tmp_path: Path) -> None:
    from mdwiki.ingest import apply_ingest_plan

    (tmp_path / "ai.md").write_text("# Attention\n\n## Intro\n\nThis paper introduces attention sinks for long contexts.\n")
    init_wiki(tmp_path, pointers=())
    with connect(tmp_path / ".mdwiki" / "state.db") as conn:
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
                    "content": "# Attention Sinks\n\nFirst body.\n",
                    "claims": [{"source_section_id": "ai.md/Intro", "quote": "this paper introduces attention sinks for long contexts"}],
                }
            ],
            "cross_refs": [],
        }
    )
    apply_ingest_plan(tmp_path, source_id=sid, original_path="ai.md", plan=plan, embedder=None)
    first = (tmp_path / "wiki/concepts/attention-sinks.md").read_text()
    meta, body = read_frontmatter(first)
    assert meta["type"] == "concept" and meta["sources"] == [sid] and meta["tags"] == ["concept"] and meta["title"] == "Attention Sinks"
    assert extract_previous_hash(first) is None
    assert body == "# Attention Sinks\n\nFirst body.\n"

    update = parse_plan_dict(
        {
            "verdict": "ingest",
            "rationale": "x",
            "updates": [
                {
                    "page": "wiki/concepts/attention-sinks.md",
                    "content": "# Attention Sinks\n\nSecond body.\n",
                    "claims": [{"source_section_id": "ai.md/Intro", "quote": "this paper introduces attention sinks for long contexts"}],
                }
            ],
            "new_pages": [],
            "cross_refs": [],
        }
    )
    apply_ingest_plan(tmp_path, source_id=sid, original_path="ai.md", plan=update, embedder=None)
    second = (tmp_path / "wiki/concepts/attention-sinks.md").read_text()
    meta2, body2 = read_frontmatter(second)
    assert body2 == "# Attention Sinks\n\nSecond body.\n"
    assert meta2["created"] == meta["created"] and meta2["sources"] == [sid]
    assert extract_previous_hash(second) == compute_body_hash(first)

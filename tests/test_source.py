"""Tests for ``mdwiki.source`` — inspect a single registered source by id/prefix."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from mdwiki.init import init_wiki
from mdwiki.source import SourceInfo, find_matching_sources, format_source_info, get_source_info
from mdwiki.state import connect


@pytest.fixture
def wiki_with_three(tmp_path: Path) -> Path:
    (tmp_path / "alpha.md").write_text("# Alpha\nbody")
    (tmp_path / "beta.md").write_text("# Beta\nbody")
    (tmp_path / "gamma.md").write_text("# Gamma\nbody")
    init_wiki(tmp_path)
    return tmp_path


@pytest.mark.unit
def test_find_matching_sources_returns_single_for_full_hash(wiki_with_three: Path) -> None:
    db_path = wiki_with_three / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        first = conn.execute("SELECT id FROM sources LIMIT 1").fetchone()["id"]
    matches = find_matching_sources(wiki_with_three, first)
    assert matches == [first]


@pytest.mark.unit
def test_find_matching_sources_supports_short_prefix(wiki_with_three: Path) -> None:
    db_path = wiki_with_three / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        first = conn.execute("SELECT id FROM sources LIMIT 1").fetchone()["id"]
    matches = find_matching_sources(wiki_with_three, first[:4])
    assert first in matches


@pytest.mark.unit
def test_find_matching_sources_empty_when_no_match(wiki_with_three: Path) -> None:
    matches = find_matching_sources(wiki_with_three, "deadbeef0000")
    assert matches == []


@pytest.mark.unit
def test_get_source_info_returns_metadata(wiki_with_three: Path) -> None:
    db_path = wiki_with_three / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        row = conn.execute("SELECT id FROM sources WHERE original_path = 'alpha.md'").fetchone()
    info = get_source_info(wiki_with_three, row["id"])
    assert info.short_hash == row["id"]
    assert info.original_path == "alpha.md"
    assert info.raw_path.startswith("raw/")
    assert info.status == "pending"
    assert info.ingested_at is None
    assert info.dependent_pages == ()


@pytest.mark.unit
def test_get_source_info_raises_for_unknown_hash(wiki_with_three: Path) -> None:
    with pytest.raises(LookupError):
        get_source_info(wiki_with_three, "doesnotexist")


@pytest.mark.unit
def test_get_source_info_lists_dependent_pages_via_backrefs(wiki_with_three: Path) -> None:
    db_path = wiki_with_three / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        source_id = conn.execute("SELECT id FROM sources WHERE original_path = 'alpha.md'").fetchone()["id"]
        conn.execute(
            "INSERT INTO pages (path, kind, last_touched_at) VALUES (?, ?, ?)",
            ("wiki/concepts/alpha-concept.md", "concept", time.time()),
        )
        conn.execute(
            "INSERT INTO pages (path, kind, last_touched_at) VALUES (?, ?, ?)",
            ("wiki/entities/alpha-paper.md", "entity", time.time()),
        )
        conn.execute(
            "INSERT INTO backrefs (page_path, source_id, quote) VALUES (?, ?, ?)",
            ("wiki/concepts/alpha-concept.md", source_id, "first quote"),
        )
        conn.execute(
            "INSERT INTO backrefs (page_path, source_id, quote) VALUES (?, ?, ?)",
            ("wiki/entities/alpha-paper.md", source_id, "second quote"),
        )
    info = get_source_info(wiki_with_three, source_id)
    assert set(info.dependent_pages) == {"wiki/concepts/alpha-concept.md", "wiki/entities/alpha-paper.md"}


@pytest.mark.unit
def test_format_source_info_contains_key_fields() -> None:
    info = SourceInfo(
        short_hash="abc123def456",
        original_path="ai-agents/foo.md",
        raw_path="raw/abc123def456-foo.md",
        content_hash="abc123def456" + "0" * 52,
        status="pending",
        ingested_at=None,
        dependent_pages=(),
    )
    out = format_source_info(info)
    assert "abc123def456" in out
    assert "ai-agents/foo.md" in out
    assert "raw/abc123def456-foo.md" in out
    assert "pending" in out
    assert "never" in out.lower()

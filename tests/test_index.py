"""Tests for ``mdwiki.index`` — generate ``wiki/index.md`` from current wiki state."""

from __future__ import annotations

from pathlib import Path

import pytest

from mdwiki.index import build_index, regenerate_index, summarize_first_paragraph
from mdwiki.init import init_wiki


@pytest.fixture
def wiki(tmp_path: Path) -> Path:
    init_wiki(tmp_path)
    return tmp_path


@pytest.mark.unit
def test_summarize_first_paragraph_returns_short_excerpt() -> None:
    page = "# Title\n\nThe first paragraph is the summary that gets extracted.\n\n## Section\n\nMore."
    assert summarize_first_paragraph(page) == "The first paragraph is the summary that gets extracted."


@pytest.mark.unit
def test_summarize_first_paragraph_strips_markdown_links() -> None:
    page = "# Title\n\nA paragraph with [a link](url) and **bold**."
    summary = summarize_first_paragraph(page)
    assert "[" not in summary
    assert "**" not in summary
    assert "a link" in summary


@pytest.mark.unit
def test_summarize_first_paragraph_truncates_long_paragraphs() -> None:
    long = "# Title\n\n" + ("the quick brown fox jumps over the lazy dog. " * 20)
    summary = summarize_first_paragraph(long, max_chars=120)
    assert len(summary) <= 123  # 120 + "..."
    assert summary.endswith("...")


@pytest.mark.unit
def test_summarize_first_paragraph_handles_no_h1() -> None:
    page = "Just prose without any heading at all here."
    assert "Just prose" in summarize_first_paragraph(page)


@pytest.mark.unit
def test_summarize_first_paragraph_returns_empty_for_empty_input() -> None:
    assert summarize_first_paragraph("") == ""
    assert summarize_first_paragraph("# Just heading") == ""


@pytest.mark.unit
def test_build_index_groups_pages_by_kind(wiki: Path) -> None:
    (wiki / "wiki" / "entities").mkdir(parents=True)
    (wiki / "wiki" / "concepts").mkdir(parents=True)
    (wiki / "wiki" / "syntheses").mkdir(parents=True)
    (wiki / "wiki" / "entities" / "alice.md").write_text("# Alice\n\nAlice is a researcher.")
    (wiki / "wiki" / "concepts" / "attention.md").write_text("# Attention\n\nA mechanism for scoring tokens.")
    (wiki / "wiki" / "syntheses" / "transformers.md").write_text("# Transformers\n\nA cross-cutting writeup.")

    text = build_index(wiki)

    assert "## Entities" in text
    assert "## Concepts" in text
    assert "## Syntheses" in text
    assert "Alice" in text
    assert "Attention" in text
    assert "Transformers" in text
    assert "(none yet)" not in text  # all sections populated


@pytest.mark.unit
def test_build_index_marks_empty_sections(wiki: Path) -> None:
    (wiki / "wiki" / "entities").mkdir(parents=True)
    (wiki / "wiki" / "entities" / "alice.md").write_text("# Alice\n\nbio")
    text = build_index(wiki)
    assert "Alice" in text
    assert "(none yet)" in text  # concepts and syntheses are empty


@pytest.mark.unit
def test_build_index_includes_summaries(wiki: Path) -> None:
    (wiki / "wiki" / "concepts").mkdir(parents=True)
    (wiki / "wiki" / "concepts" / "attention.md").write_text(
        "# Attention\n\nA mechanism for weighting tokens by relevance."
    )
    text = build_index(wiki)
    assert "weighting tokens" in text


@pytest.mark.unit
def test_build_index_omits_index_log_themselves(wiki: Path) -> None:
    """index.md and log.md must never appear inside index.md."""
    (wiki / "wiki" / "concepts").mkdir(parents=True)
    (wiki / "wiki" / "concepts" / "x.md").write_text("# x\n\ny")
    (wiki / "wiki" / "index.md").write_text("# stale index")
    (wiki / "wiki" / "log.md").write_text("- old line")

    text = build_index(wiki)

    assert "index.md" not in text
    assert "log.md" not in text


@pytest.mark.unit
def test_regenerate_index_writes_to_wiki_index_md(wiki: Path) -> None:
    (wiki / "wiki" / "entities").mkdir(parents=True)
    (wiki / "wiki" / "entities" / "alice.md").write_text("# Alice\n\nbio")
    regenerate_index(wiki)
    contents = (wiki / "wiki" / "index.md").read_text()
    assert "Alice" in contents
    assert "Wiki Index" in contents


@pytest.mark.unit
def test_regenerate_index_overwrites_existing_index(wiki: Path) -> None:
    (wiki / "wiki" / "concepts").mkdir(parents=True)
    (wiki / "wiki" / "concepts" / "x.md").write_text("# x\n\ny")
    (wiki / "wiki" / "index.md").write_text("# OLD STALE INDEX\n\nbad bad")
    regenerate_index(wiki)
    contents = (wiki / "wiki" / "index.md").read_text()
    assert "OLD STALE INDEX" not in contents
    assert "x" in contents

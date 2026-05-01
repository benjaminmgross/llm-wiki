"""Tests for the chunker's fallback tiers (Phase 3).

When a markdown file has no H2 headers, the chunker falls back through:
  H1 → paragraph splits → fixed-token sliding windows.

This is essential for transcripts (often one giant block of speaker turns
with no H2s) and for many real-world markdown files that don't conform to
H2-only structure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mdwiki.chunker import MarkdownChunker


@pytest.mark.unit
def test_chunk_file_falls_back_to_h1_when_no_h2(tmp_path: Path) -> None:
    """A file with H1 headers but no H2 chunks into multiple H1-bounded sections."""
    md = tmp_path / "doc.md"
    md.write_text(
        "# First\n\nbody one with enough words to meet the minimum requirement here.\n\n"
        "# Second\n\nbody two with enough words to meet the minimum requirement here.\n\n"
        "# Third\n\nbody three with enough words to meet the minimum requirement here.\n"
    )

    chunker = MarkdownChunker(min_section_words=5)
    sections = chunker.chunk_file(md)

    assert len(sections) == 3
    headings = [s["heading"] for s in sections]
    assert headings == ["First", "Second", "Third"]


@pytest.mark.unit
def test_chunk_file_falls_back_to_paragraphs_when_no_headers(tmp_path: Path) -> None:
    """A file with no headers at all chunks on paragraph boundaries (\\n\\n)."""
    md = tmp_path / "flat.md"
    body_a = "first paragraph " * 10
    body_b = "second paragraph " * 10
    body_c = "third paragraph " * 10
    md.write_text(f"{body_a.strip()}\n\n{body_b.strip()}\n\n{body_c.strip()}\n")

    chunker = MarkdownChunker(min_section_words=5)
    sections = chunker.chunk_file(md)

    assert len(sections) == 3
    assert "first paragraph" in sections[0]["content"]
    assert "second paragraph" in sections[1]["content"]
    assert "third paragraph" in sections[2]["content"]


@pytest.mark.unit
def test_chunk_file_falls_back_to_sliding_window_for_one_big_block(tmp_path: Path) -> None:
    """A file with no headers and no paragraph breaks falls back to fixed-token windows."""
    md = tmp_path / "huge.md"
    # ~3000 words on a single line → must be windowed.
    md.write_text("word " * 3000)

    chunker = MarkdownChunker(min_section_words=5)
    sections = chunker.chunk_file(md)

    # 3000 words / 800-word window = ~4 sections (with overlap).
    assert len(sections) >= 2
    assert all(s["content"].strip() for s in sections)


@pytest.mark.unit
def test_chunk_file_h2_still_wins_when_present(tmp_path: Path) -> None:
    """H2 chunking is preferred over fallback tiers — fallback only fires when H2 yields zero sections."""
    md = tmp_path / "mixed.md"
    md.write_text(
        "# H1 Title\n\n"
        "## Section A\n\nbody A with enough words to meet the minimum threshold.\n\n"
        "## Section B\n\nbody B with enough words to meet the minimum threshold.\n"
    )

    chunker = MarkdownChunker(min_section_words=5)
    sections = chunker.chunk_file(md)

    assert len(sections) == 2
    assert sections[0]["heading"] == "Section A"
    assert sections[1]["heading"] == "Section B"


@pytest.mark.unit
def test_chunk_file_records_chunking_strategy_in_sections(tmp_path: Path) -> None:
    """Each section records which fallback tier produced it (h2/h1/paragraph/window)."""
    md_h2 = tmp_path / "h2.md"
    md_h2.write_text("## Foo\n\nbody body body body body body body body body body.\n")

    md_h1 = tmp_path / "h1.md"
    md_h1.write_text("# Foo\n\nbody body body body body body body body body body.\n")

    chunker = MarkdownChunker(min_section_words=5)
    sec_h2 = chunker.chunk_file(md_h2)
    sec_h1 = chunker.chunk_file(md_h1)

    assert sec_h2[0]["chunking_strategy"] == "h2"
    assert sec_h1[0]["chunking_strategy"] == "h1"

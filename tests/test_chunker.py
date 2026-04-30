"""Tests for the chunker module."""


def test_chunk_file_extracts_h2_sections():
    """
    Given: A markdown file with multiple H2 sections
    When: Chunking the file
    Then: Each H2 becomes a separate section with metadata
    """
    import tempfile
    from pathlib import Path

    from mdwiki.chunker import MarkdownChunker

    with tempfile.TemporaryDirectory() as tmpdir:
        md_file = Path(tmpdir) / "test.md"
        md_file.write_text("""# Main Title

Some intro text.

## First Section

Content for first section. This is a full paragraph with enough words
to meet the minimum word count requirement for section extraction.

## Second Section

Content for second section. More content here to ensure we have
sufficient text for the section to be included in the output.

## Third Section

Final content goes here. We need enough words in each section to
make sure the chunker includes them in the extracted sections list.
""")

        chunker = MarkdownChunker()
        sections = chunker.chunk_file(md_file)

        assert len(sections) == 3
        assert sections[0]['heading'] == "First Section"
        assert sections[1]['heading'] == "Second Section"
        assert sections[2]['heading'] == "Third Section"
        assert "Content for first section" in sections[0]['content']
        assert sections[0]['source_file'] == str(md_file)
        assert sections[0]['section_id'] == "test.md/First Section"


def test_chunk_directory_processes_all_files():
    """
    Given: A directory with multiple markdown files
    When: Chunking the directory
    Then: All H2 sections from all files are returned
    """
    import tempfile
    from pathlib import Path

    from mdwiki.chunker import MarkdownChunker

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        (tmppath / "a.md").write_text(
            "# A\n## A1\nContent A1 with enough words to meet minimum requirements for chunking.\n"
            "## A2\nContent A2 also with enough words to meet minimum requirements for chunking."
        )
        (tmppath / "b.md").write_text(
            "# B\n## B1\nContent B1 with sufficient word count to be included in the output."
        )

        chunker = MarkdownChunker()
        sections = chunker.chunk_directory(tmppath)

        assert len(sections) == 3
        headings = {s['heading'] for s in sections}
        assert headings == {"A1", "A2", "B1"}


def test_chunk_file_skips_short_sections():
    """
    Given: A file with sections below minimum word count
    When: Chunking with min_section_words=10
    Then: Short sections are excluded
    """
    import tempfile
    from pathlib import Path

    from mdwiki.chunker import MarkdownChunker

    with tempfile.TemporaryDirectory() as tmpdir:
        md_file = Path(tmpdir) / "test.md"
        md_file.write_text("""# Test
## Short

Too brief.

## Long Section

This section has enough content to meet the minimum word count requirement
for being included in the chunked output from the markdown file.
""")

        chunker = MarkdownChunker(min_section_words=10)
        sections = chunker.chunk_file(md_file)

        assert len(sections) == 1
        assert sections[0]['heading'] == "Long Section"


def test_chunk_file_strips_frontmatter():
    """
    Given: A file with YAML frontmatter
    When: Chunking the file
    Then: Frontmatter is stripped from content
    """
    import tempfile
    from pathlib import Path

    from mdwiki.chunker import MarkdownChunker

    with tempfile.TemporaryDirectory() as tmpdir:
        md_file = Path(tmpdir) / "test.md"
        md_file.write_text("""---
title: Test Document
date: 2025-01-01
---

# Main Title

## Content Section

This is the actual content of the document with enough words to meet
the minimum word count requirement for section extraction by the chunker.
""")

        chunker = MarkdownChunker()
        sections = chunker.chunk_file(md_file)

        assert len(sections) == 1
        assert "title:" not in sections[0]['content']
        assert "date:" not in sections[0]['content']
        assert "This is the actual content" in sections[0]['content']


def test_chunk_file_generates_fingerprints():
    """
    Given: Two sections with identical content
    When: Chunking both
    Then: They have the same fingerprint
    """
    import tempfile
    from pathlib import Path

    from mdwiki.chunker import MarkdownChunker

    with tempfile.TemporaryDirectory() as tmpdir:
        md_file1 = Path(tmpdir) / "file1.md"
        md_file2 = Path(tmpdir) / "file2.md"

        # Same content, different headings
        md_file1.write_text("# Doc1\n## Section\nIdentical content here.")
        md_file2.write_text("# Doc2\n## Other Name\nIdentical content here.")

        chunker = MarkdownChunker(min_section_words=1)
        sections1 = chunker.chunk_file(md_file1)
        sections2 = chunker.chunk_file(md_file2)

        assert sections1[0]['fingerprint'] == sections2[0]['fingerprint']


def test_chunk_file_includes_metadata():
    """
    Given: A markdown file
    When: Chunking it
    Then: Each section includes word_count and last_modified
    """
    import tempfile
    from pathlib import Path

    from mdwiki.chunker import MarkdownChunker

    with tempfile.TemporaryDirectory() as tmpdir:
        md_file = Path(tmpdir) / "test.md"
        md_file.write_text("# Test\n## Section\nOne two three four five six seven eight nine ten.")

        chunker = MarkdownChunker(min_section_words=1)
        sections = chunker.chunk_file(md_file)

        assert len(sections) == 1
        assert 'word_count' in sections[0]
        assert sections[0]['word_count'] == 10
        assert 'last_modified' in sections[0]
        assert sections[0]['last_modified']  # Not empty

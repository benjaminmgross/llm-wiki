"""
Tests for markdown-consolidator.
"""

import tempfile
from pathlib import Path

import pytest


def test_version():
    """Test that version is accessible."""
    from mdwiki import __version__
    assert __version__ == "1.0.0"


def test_inventory_empty_directory():
    """Test inventory on empty directory."""
    from mdwiki import inventory_directory

    with tempfile.TemporaryDirectory() as tmpdir:
        result = inventory_directory(directory=Path(tmpdir))
        assert result == []


def test_inventory_single_file():
    """Test inventory with a single markdown file."""
    from mdwiki import inventory_directory

    with tempfile.TemporaryDirectory() as tmpdir:
        md_file = Path(tmpdir) / "test.md"
        md_file.write_text("# Test\n\nHello world")

        result = inventory_directory(directory=Path(tmpdir))

        assert len(result) == 1
        assert result[0]['filename'] == 'test.md'
        assert result[0]['title'] == 'Test'
        assert result[0]['word_count'] >= 2  # "Hello world" at minimum


def test_extract_frontmatter():
    """Test frontmatter extraction."""
    from mdwiki.inventory import extract_frontmatter

    content = """---
title: Test Document
tags: [a, b]
---

# Content here
"""
    frontmatter, body = extract_frontmatter(content=content)

    assert frontmatter['title'] == 'Test Document'
    assert frontmatter['tags'] == ['a', 'b']
    assert '# Content here' in body


def test_extract_links():
    """Test link extraction."""
    from mdwiki.inventory import extract_links

    content = """
Check [[wikilink]] and [[another|display]].
Also [markdown](link.md) and [external](https://example.com).
"""
    links = extract_links(content=content)

    assert 'wikilink' in links['internal']
    assert 'another' in links['internal']
    assert 'link.md' in links['internal']
    assert len(links['external']) == 1
    assert links['external'][0]['url'] == 'https://example.com'


def test_compute_fingerprint():
    """Test content fingerprinting."""
    from mdwiki.inventory import compute_fingerprint

    fp1 = compute_fingerprint(content="Hello World!")
    fp2 = compute_fingerprint(content="hello world")
    fp3 = compute_fingerprint(content="Something else")

    # Same content (normalized) should have same fingerprint
    assert fp1 == fp2
    # Different content should have different fingerprint
    assert fp1 != fp3


def test_cosine_similarity():
    """Test cosine similarity calculation."""
    from mdwiki.relationships import cosine_similarity

    vec1 = {'a': 1.0, 'b': 2.0}
    vec2 = {'a': 1.0, 'b': 2.0}
    vec3 = {'c': 1.0, 'd': 2.0}

    # Identical vectors
    assert cosine_similarity(vec1=vec1, vec2=vec2) == pytest.approx(1.0)
    # No overlap
    assert cosine_similarity(vec1=vec1, vec2=vec3) == 0.0


def test_topic_cluster():
    """Test topic clustering."""
    from mdwiki.clustering import topic_cluster

    relationships = {
        'content_similarities': [
            {'file1': 'a.md', 'file2': 'b.md', 'similarity': 0.8},
            {'file1': 'b.md', 'file2': 'c.md', 'similarity': 0.7},
        ]
    }

    clusters = topic_cluster(relationships=relationships, threshold=0.6)

    assert len(clusters) == 1
    assert set(clusters[0]['files']) == {'a.md', 'b.md', 'c.md'}


def test_full_pipeline():
    """Test the full consolidation pipeline."""
    from mdwiki import consolidate

    with tempfile.TemporaryDirectory() as tmpdir:
        source = Path(tmpdir) / "source"
        output = Path(tmpdir) / "output"
        source.mkdir()

        # Create test files with overlapping content
        (source / "file1.md").write_text("""---
title: File One
---
# Topic A

This is about topic A and some shared content.
""")
        (source / "file2.md").write_text("""---
title: File Two
---
# Topic A

This discusses topic A with shared content and more details.
""")

        result = consolidate(source_dir=source, output_dir=output, threshold=0.3)

        assert result['files_analyzed'] == 2
        assert result['output_directory'] == str(output)
        assert (output / '_consolidation_log.json').exists()


def test_consolidate_with_subfolders_copies_non_markdown(tmp_path: Path) -> None:
    """Test that preserve_subfolders=True copies non-markdown files."""
    # Arrange
    source = tmp_path / "source"
    (source / "subdir1").mkdir(parents=True)
    (source / "subdir2").mkdir(parents=True)

    # Create markdown files (these consolidate across directories)
    (source / "root.md").write_text("# Auth\nAuthentication overview")
    (source / "subdir1" / "a.md").write_text("# Auth Details\nMore about authentication")
    (source / "subdir2" / "c.md").write_text("# Unrelated\nDifferent topic")

    # Create non-markdown files
    (source / "subdir1" / "image.png").write_bytes(b"fake png data")
    (source / "subdir2" / "diagram.svg").write_text("<svg></svg>")

    output = tmp_path / "output"

    # Act
    from mdwiki.consolidator import consolidate

    result = consolidate(
        source_dir=source,
        output_dir=output,
        preserve_subfolders=True,
    )

    # Assert
    # Non-markdown files copied with structure preserved
    assert (output / "subdir1" / "image.png").exists()
    assert (output / "subdir2" / "diagram.svg").exists()
    assert result['non_markdown_copied'] == 2

    # Markdown files from all directories analyzed together
    assert result['files_analyzed'] == 3


def test_consolidate_without_subfolders_no_copy(tmp_path: Path) -> None:
    """Test that preserve_subfolders=False does NOT copy non-markdown files."""
    # Arrange
    source = tmp_path / "source"
    (source / "subdir").mkdir(parents=True)
    (source / "a.md").write_text("# A\nContent")
    (source / "subdir" / "b.md").write_text("# B\nContent about A")
    (source / "subdir" / "image.png").write_bytes(b"fake png")

    output = tmp_path / "output"

    # Act
    from mdwiki.consolidator import consolidate

    result = consolidate(
        source_dir=source,
        output_dir=output,
        preserve_subfolders=False,
    )

    # Assert
    # Non-markdown file NOT copied
    assert not (output / "subdir" / "image.png").exists()
    assert result['non_markdown_copied'] == 0

    # But markdown files from subdirs ARE still analyzed
    assert result['files_analyzed'] == 2


def test_consolidate_subfolders_output_inside_source(tmp_path: Path) -> None:
    """Test that output dir inside source dir doesn't cause infinite recursion."""
    # Arrange - output is a subdirectory of source (common usage pattern)
    source = tmp_path / "source"
    (source / "subdir").mkdir(parents=True)
    (source / "readme.md").write_text("# Readme\nMain content")
    (source / "subdir" / "doc.md").write_text("# Doc\nMore content")
    (source / "subdir" / "image.png").write_bytes(b"fake png data")

    # Output dir is INSIDE source dir
    output = source / "consolidated"

    # Act
    from mdwiki.consolidator import consolidate

    result = consolidate(
        source_dir=source,
        output_dir=output,
        preserve_subfolders=True,
    )

    # Assert - should complete without infinite recursion
    assert result['files_analyzed'] == 2
    assert (output / "subdir" / "image.png").exists()
    # Should NOT have nested consolidated/consolidated/... directories
    assert not (output / "consolidated").exists()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

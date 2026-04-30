"""Tests for the manifest-based synthesis functionality."""

import tempfile
from pathlib import Path


def test_synthesize_from_manifest_creates_files():
    """
    Given: A parsed manifest with sections
    When: Synthesizing from manifest
    Then: Creates markdown files with correct content
    """
    from mdwiki.synthesis import synthesize_from_manifest

    # Create source file with content
    with tempfile.TemporaryDirectory() as tmpdir:
        source_dir = Path(tmpdir) / "source"
        output_dir = Path(tmpdir) / "output"
        source_dir.mkdir()

        (source_dir / "test.md").write_text("""# Test
## OAuth Setup
This is the OAuth setup content.
## Login Flow
This is the login flow content.
""")

        manifest = {
            'source': str(source_dir),
            'hierarchy': [{
                'theme': 'Auth',
                'confidence': 0.85,
                'documents': [{
                    'name': 'authentication.md',
                    'sections': [
                        {'id': 'test.md/OAuth Setup', 'heading': 'OAuth Setup'},
                        {'id': 'test.md/Login Flow', 'heading': 'Login Flow'},
                    ]
                }]
            }],
            'orphans': []
        }

        results = synthesize_from_manifest(
            manifest=manifest,
            output_dir=output_dir,
        )

        assert len(results) == 1
        output_file = output_dir / "authentication.md"
        assert output_file.exists()
        content = output_file.read_text()
        assert "OAuth Setup" in content
        assert "Login Flow" in content


def test_synthesize_from_manifest_skips_duplicates():
    """
    Given: A manifest with sections marked as duplicates
    When: Synthesizing
    Then: Duplicate sections are not included in output
    """
    from mdwiki.synthesis import synthesize_from_manifest

    with tempfile.TemporaryDirectory() as tmpdir:
        source_dir = Path(tmpdir) / "source"
        output_dir = Path(tmpdir) / "output"
        source_dir.mkdir()

        (source_dir / "test.md").write_text("""# Test
## Section A
Content A
## Section B
Content B
""")

        manifest = {
            'source': str(source_dir),
            'hierarchy': [{
                'theme': 'Test',
                'documents': [{
                    'name': 'test.md',
                    'sections': [
                        {'id': 'test.md/Section A', 'heading': 'Section A'},
                        {'id': 'test.md/Section B', 'heading': 'Section B', 'duplicate_of': 'test.md/Section A'},
                    ]
                }]
            }],
            'orphans': []
        }

        results = synthesize_from_manifest(manifest=manifest, output_dir=output_dir)

        output_file = output_dir / "test.md"
        content = output_file.read_text()
        assert content.count("## Section A") == 1
        assert "Section B" not in content  # Duplicate should not appear


def test_synthesize_from_manifest_handles_standalone_orphans():
    """
    Given: A manifest with standalone orphan sections
    When: Synthesizing
    Then: Creates separate files for standalone orphans
    """
    from mdwiki.synthesis import synthesize_from_manifest

    with tempfile.TemporaryDirectory() as tmpdir:
        source_dir = Path(tmpdir) / "source"
        output_dir = Path(tmpdir) / "output"
        source_dir.mkdir()

        (source_dir / "test.md").write_text("""# Test
## Standalone Section
This is standalone content that should become its own file.
""")

        manifest = {
            'source': str(source_dir),
            'hierarchy': [],
            'orphans': [{
                'id': 'test.md/Standalone Section',
                'heading': 'Standalone Section',
                'suggested_action': 'standalone'
            }]
        }

        synthesize_from_manifest(manifest=manifest, output_dir=output_dir)

        # Should create a file for the standalone orphan
        orphan_files = list(output_dir.glob("*.md"))
        assert len(orphan_files) == 1
        content = orphan_files[0].read_text()
        assert "Standalone Section" in content


def test_synthesize_from_manifest_empty_hierarchy():
    """
    Given: A manifest with no themes or orphans
    When: Synthesizing
    Then: Returns empty results without errors
    """
    from mdwiki.synthesis import synthesize_from_manifest

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir) / "output"

        manifest = {
            'source': str(tmpdir),
            'hierarchy': [],
            'orphans': []
        }

        results = synthesize_from_manifest(manifest=manifest, output_dir=output_dir)

        assert results == []

"""Tests for threshold consistency across the pipeline."""


def test_threshold_flows_consistently():
    """
    Given: Files with similarity scores above 0.35
    When: Running consolidation with threshold=0.35
    Then: Files are clustered (not 0 clusters)

    This tests that the threshold value flows consistently through the pipeline:
    - relationships.py uses it to find similar files
    - clustering.py uses it to build clusters
    """
    import tempfile
    from pathlib import Path

    from mdwiki.consolidator import consolidate

    with tempfile.TemporaryDirectory() as tmpdir:
        source = Path(tmpdir) / "source"
        output = Path(tmpdir) / "output"
        source.mkdir()

        # Create files with moderate-to-high similarity
        # These have significant word overlap to ensure similarity > 0.35
        (source / "auth-login.md").write_text(
            "# Auth\n## Login\nUser authentication login flow with OAuth tokens. "
            "Handle user credentials, session management, and secure authentication."
        )
        (source / "auth-oauth.md").write_text(
            "# Auth\n## OAuth\nOAuth authentication setup with login redirect. "
            "Configure user credentials, token management, and secure authentication flow."
        )
        (source / "unrelated.md").write_text(
            "# Database\n## Schema\nPostgreSQL table design patterns for data storage."
        )

        result = consolidate(
            source_dir=source,
            output_dir=output,
            threshold=0.35,
        )

        # Should have at least 1 cluster (the two auth files)
        assert (
            result['clusters_created'] >= 1
        ), f"Expected clusters, got {result['clusters_created']}"


def test_threshold_consistency_between_modules():
    """
    Given: A specific threshold value
    When: Used in both analyze_relationships and cluster_files
    Then: The same threshold value is used consistently
    """
    import tempfile
    from pathlib import Path

    from mdwiki.clustering import cluster_files
    from mdwiki.inventory import inventory_directory
    from mdwiki.relationships import analyze_relationships

    with tempfile.TemporaryDirectory() as tmpdir:
        source = Path(tmpdir)

        # Create files with known similarity pattern
        (source / "doc1.md").write_text(
            "# Python Guide\n## Introduction\nPython programming language fundamentals. "
            "Learn Python basics, syntax, and core concepts for programming."
        )
        (source / "doc2.md").write_text(
            "# Python Tutorial\n## Getting Started\nPython programming basics tutorial. "
            "Master Python fundamentals, syntax patterns, and programming concepts."
        )

        files = inventory_directory(directory=source, exclude_patterns=[])
        inventory = {'source_directory': str(source), 'files': files}

        # Test with threshold=0.3 - should find pairs
        rels = analyze_relationships(inventory=inventory, threshold=0.3)
        clusters = cluster_files(relationships=rels, threshold=0.3)

        # Should find similarities and create clusters at same threshold
        assert len(rels['content_similarities']) >= 1, "Should find similar content"
        assert len(clusters) >= 1, "Should create clusters from similar content"

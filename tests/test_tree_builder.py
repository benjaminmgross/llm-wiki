"""Tests for the tree builder module."""


def test_build_hierarchy_creates_themes():
    """
    Given: Sections with embeddings
    When: Building hierarchy
    Then: Returns themes containing documents containing sections
    """
    from mdwiki.tree_builder import TreeBuilder

    # Create mock sections with embeddings
    # Two auth-related, one database-related
    sections = [
        {
            'section_id': 'a',
            'heading': 'OAuth Setup',
            'embedding': [1.0, 0.0, 0.0] + [0.0] * 381,
            'keywords': ['oauth'],
            'fingerprint': 'aaa',
            'last_modified': '2025-12-08',
        },
        {
            'section_id': 'b',
            'heading': 'Login Flow',
            'embedding': [0.9, 0.1, 0.0] + [0.0] * 381,
            'keywords': ['login'],
            'fingerprint': 'bbb',
            'last_modified': '2025-12-08',
        },
        {
            'section_id': 'c',
            'heading': 'Schema Design',
            'embedding': [0.0, 0.0, 1.0] + [0.0] * 381,
            'keywords': ['database'],
            'fingerprint': 'ccc',
            'last_modified': '2025-12-08',
        },
    ]

    builder = TreeBuilder(threshold=0.5)
    hierarchy = builder.build_hierarchy(sections)

    assert 'themes' in hierarchy
    assert len(hierarchy['themes']) >= 1
    # Auth sections should cluster together


def test_deduplication_marks_duplicates():
    """
    Given: Sections with identical fingerprints
    When: Building hierarchy
    Then: Newer one is kept, older marked as duplicate
    """
    from mdwiki.tree_builder import TreeBuilder

    sections = [
        {
            'section_id': 'a',
            'heading': 'OAuth',
            'embedding': [1.0] * 384,
            'fingerprint': 'same',
            'last_modified': '2025-12-08',
            'keywords': [],
        },
        {
            'section_id': 'b',
            'heading': 'OAuth Copy',
            'embedding': [1.0] * 384,
            'fingerprint': 'same',
            'last_modified': '2025-12-01',
            'keywords': [],
        },
    ]

    builder = TreeBuilder()
    hierarchy = builder.build_hierarchy(sections)

    # Find the duplicate marker
    all_sections = []
    for theme in hierarchy['themes']:
        for doc in theme['documents']:
            all_sections.extend(doc['sections'])

    duplicates = [s for s in all_sections if s.get('duplicate_of')]
    assert len(duplicates) == 1
    assert duplicates[0]['id'] == 'b'  # Older one is duplicate


def test_empty_sections_returns_empty_hierarchy():
    """
    Given: Empty list of sections
    When: Building hierarchy
    Then: Returns structure with empty themes and orphans
    """
    from mdwiki.tree_builder import TreeBuilder

    builder = TreeBuilder()
    hierarchy = builder.build_hierarchy([])

    assert hierarchy == {'themes': [], 'orphans': []}


def test_single_section_creates_single_theme():
    """
    Given: A single section
    When: Building hierarchy
    Then: Returns a theme with one document
    """
    from mdwiki.tree_builder import TreeBuilder

    sections = [
        {
            'section_id': 'a',
            'heading': 'OAuth',
            'embedding': [1.0] * 384,
            'fingerprint': 'xxx',
            'last_modified': '2025-12-08',
            'keywords': ['oauth'],
        },
    ]

    builder = TreeBuilder()
    hierarchy = builder.build_hierarchy(sections)

    assert len(hierarchy['themes']) == 1
    assert len(hierarchy['themes'][0]['documents']) == 1


def test_hierarchy_includes_section_metadata():
    """
    Given: Sections with full metadata
    When: Building hierarchy
    Then: Formatted sections include id, heading, keywords, source, modified
    """
    from mdwiki.tree_builder import TreeBuilder

    sections = [
        {
            'section_id': 'test.md/OAuth Setup',
            'heading': 'OAuth Setup',
            'embedding': [1.0] * 384,
            'fingerprint': 'xxx',
            'last_modified': '2025-12-08T10:30:00',
            'keywords': ['oauth', 'auth'],
            'source_file': '/path/to/test.md',
            'summary': 'Explains OAuth setup',
        },
    ]

    builder = TreeBuilder()
    hierarchy = builder.build_hierarchy(sections)

    section = hierarchy['themes'][0]['documents'][0]['sections'][0]
    assert section['id'] == 'test.md/OAuth Setup'
    assert section['heading'] == 'OAuth Setup'
    assert section['keywords'] == ['oauth', 'auth']
    assert section['modified'] == '2025-12-08'
    assert section['summary'] == 'Explains OAuth setup'

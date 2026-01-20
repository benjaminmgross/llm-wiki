"""
Tests for encapsulation scoring module.
"""

import pytest


def test_encapsulation_scorer_high_coherence():
    """
    Given: A header that accurately describes its content
    When: Computing encapsulation score
    Then: Score should be > 0.7
    """
    from markdown_consolidator.encapsulation import EncapsulationScorer

    scorer = EncapsulationScorer()

    score = scorer.score_encapsulation(
        header="User Authentication Flow",
        content="This section describes how users authenticate using OAuth2. "
                "The flow involves redirecting to the identity provider, "
                "receiving an authorization code, and exchanging it for tokens.",
    )

    assert score > 0.7


def test_encapsulation_scorer_low_coherence():
    """
    Given: A generic header that doesn't describe specific content
    When: Computing encapsulation score
    Then: Score should be < 0.5
    """
    from markdown_consolidator.encapsulation import EncapsulationScorer

    scorer = EncapsulationScorer()

    score = scorer.score_encapsulation(
        header="Notes",
        content="The database migration requires PostgreSQL 14 or higher. "
                "Make sure to backup all data before running the migration script. "
                "The estimated downtime is 30 minutes for large datasets.",
    )

    assert score < 0.5


def test_encapsulate_sections_adds_scores():
    """
    Given: A list of sections with heading and content
    When: Running encapsulate_sections
    Then: Each section has encapsulation_score key added
    """
    from markdown_consolidator.encapsulation import EncapsulationScorer

    sections = [
        {
            'section_id': 'doc1/OAuth Setup',
            'heading': 'OAuth Setup',
            'content': 'Configure OAuth by registering your application...',
        },
        {
            'section_id': 'doc1/Notes',
            'heading': 'Notes',
            'content': 'Database requires PostgreSQL 14. Also check memory limits.',
        },
    ]

    scorer = EncapsulationScorer()
    result = scorer.encapsulate_sections(sections)

    assert len(result) == 2
    assert 'encapsulation_score' in result[0]
    assert 'encapsulation_score' in result[1]
    assert isinstance(result[0]['encapsulation_score'], float)
    assert 0.0 <= result[0]['encapsulation_score'] <= 1.0
    # Verify OAuth section scores higher than generic "Notes"
    assert result[0]['encapsulation_score'] > result[1]['encapsulation_score']


def test_encapsulate_sections_empty_input():
    """
    Given: An empty list of sections
    When: Running encapsulate_sections
    Then: Returns empty list without error
    """
    from markdown_consolidator.encapsulation import EncapsulationScorer

    scorer = EncapsulationScorer()
    result = scorer.encapsulate_sections([])

    assert result == []


def test_rechunker_splits_mixed_content(mocker):
    """
    Given: A section with mixed topics and poor encapsulation
    When: Rechunking with LLM
    Then: Returns multiple sections with better encapsulation
    """
    from markdown_consolidator.encapsulation import Rechunker

    # Mock Claude response
    mock_content = mocker.Mock()
    mock_content.text = '''[
            {"header": "Database Requirements", "content": "PostgreSQL 14 required."},
            {"header": "Memory Configuration", "content": "Set memory limits to 4GB."}
        ]'''
    mock_message = mocker.Mock()
    mock_message.content = [mock_content]

    mock_client = mocker.Mock()
    mock_client.messages.create.return_value = mock_message
    mocker.patch('anthropic.Anthropic', return_value=mock_client)

    section = {
        'section_id': 'doc/Notes',
        'heading': 'Notes',
        'content': 'PostgreSQL 14 required. Set memory limits to 4GB.',
        'source_file': 'doc.md',
        'encapsulation_score': 0.3,
    }

    rechunker = Rechunker(api_key='test-key')
    result = rechunker.rechunk_section(section)

    assert len(result) >= 2
    assert result[0]['heading'] == 'Database Requirements'
    assert result[1]['heading'] == 'Memory Configuration'


def test_rechunker_skips_well_encapsulated(mocker):
    """
    Given: A section with good encapsulation score
    When: Attempting to rechunk
    Then: Returns original section unchanged
    """
    from markdown_consolidator.encapsulation import Rechunker

    section = {
        'section_id': 'doc/OAuth',
        'heading': 'OAuth Authentication',
        'content': 'OAuth authentication flow...',
        'encapsulation_score': 0.85,
    }

    rechunker = Rechunker(threshold=0.5)
    result = rechunker.rechunk_section(section)

    # Should not call LLM, returns original
    assert len(result) == 1
    assert result[0] == section

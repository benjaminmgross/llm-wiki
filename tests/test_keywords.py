"""Tests for the keywords module."""


def test_extract_keywords_from_sections():
    """
    Given: Multiple sections with content
    When: Extracting keywords
    Then: Each section has relevant keywords
    """
    from mdwiki.keywords import KeywordExtractor

    sections = [
        {
            'section_id': 'a',
            'content': 'OAuth authentication requires client credentials and redirect URIs for token exchange',
        },
        {
            'section_id': 'b',
            'content': 'Database schema design uses foreign keys and indexes for query optimization',
        },
    ]

    extractor = KeywordExtractor(max_keywords=5)
    result = extractor.extract_keywords(sections)

    assert 'keywords' in result[0]
    assert len(result[0]['keywords']) <= 5
    assert any('oauth' in kw.lower() or 'auth' in kw.lower() for kw in result[0]['keywords'])


def test_extract_keywords_handles_empty_input():
    """
    Given: Empty list of sections
    When: Extracting keywords
    Then: Returns empty list without error
    """
    from mdwiki.keywords import KeywordExtractor

    extractor = KeywordExtractor()
    result = extractor.extract_keywords([])

    assert result == []


def test_extract_keywords_handles_single_section():
    """
    Given: A single section
    When: Extracting keywords
    Then: Returns keywords without error (may be limited due to TF-IDF)
    """
    from mdwiki.keywords import KeywordExtractor

    sections = [
        {
            'section_id': 'a',
            'content': 'Authentication OAuth tokens credentials login user session',
        },
    ]

    extractor = KeywordExtractor(max_keywords=5)
    result = extractor.extract_keywords(sections)

    # With single document, TF-IDF may return limited results
    assert 'keywords' in result[0]
    assert isinstance(result[0]['keywords'], list)


def test_extract_keywords_max_limit():
    """
    Given: A section with many potential keywords
    When: Extracting with max_keywords=3
    Then: Returns at most 3 keywords
    """
    from mdwiki.keywords import KeywordExtractor

    sections = [
        {
            'section_id': 'a',
            'content': 'Python Java JavaScript TypeScript Ruby Golang Rust C++ C# PHP Kotlin Swift',
        },
        {
            'section_id': 'b',
            'content': 'Python data science machine learning deep learning artificial intelligence',
        },
    ]

    extractor = KeywordExtractor(max_keywords=3)
    result = extractor.extract_keywords(sections)

    for section in result:
        assert len(section['keywords']) <= 3

"""Tests for the embedder module."""
import pytest


def test_embedder_generates_vectors():
    """
    Given: A list of sections with content
    When: Embedding the sections
    Then: Each section has a 384-dim embedding vector
    """
    from mdwiki.embedder import Embedder

    sections = [
        {'section_id': 'a', 'content': 'OAuth authentication setup guide'},
        {'section_id': 'b', 'content': 'Database schema design patterns'},
    ]

    embedder = Embedder()
    embedded = embedder.embed_sections(sections)

    assert len(embedded) == 2
    assert 'embedding' in embedded[0]
    assert len(embedded[0]['embedding']) == 384
    assert isinstance(embedded[0]['embedding'], list)


def test_similar_content_has_similar_embeddings():
    """
    Given: Two semantically similar sections and one different
    When: Computing embeddings
    Then: Similar sections have higher cosine similarity
    """
    import numpy as np

    from mdwiki.embedder import Embedder

    sections = [
        {'section_id': 'auth1', 'content': 'User authentication with OAuth tokens'},
        {'section_id': 'auth2', 'content': 'OAuth token authentication for users'},
        {'section_id': 'db', 'content': 'PostgreSQL database schema design'},
    ]

    embedder = Embedder()
    embedded = embedder.embed_sections(sections)

    def cosine_sim(a, b):
        return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

    auth_sim = cosine_sim(embedded[0]['embedding'], embedded[1]['embedding'])
    diff_sim = cosine_sim(embedded[0]['embedding'], embedded[2]['embedding'])

    assert auth_sim > diff_sim, f"Expected auth similarity {auth_sim} > {diff_sim}"


def test_embedder_handles_empty_input():
    """
    Given: An empty list of sections
    When: Embedding
    Then: Returns empty list without error
    """
    from mdwiki.embedder import Embedder

    embedder = Embedder()
    embedded = embedder.embed_sections([])

    assert embedded == []


def test_embed_text_single_string():
    """
    Given: A single text string
    When: Embedding it
    Then: Returns a vector of correct dimension
    """
    from mdwiki.embedder import Embedder

    embedder = Embedder()
    vector = embedder.embed_text("Test document about authentication")

    assert isinstance(vector, list)
    assert len(vector) == 384

"""Tests for the summarizer module."""
import pytest


def test_summarizer_generates_summaries(mocker):
    """
    Given: Sections with content
    When: Summarizing with Claude
    Then: Each section gets a summary
    """
    from markdown_consolidator.summarizer import Summarizer

    # Mock Claude response
    mock_content = mocker.Mock()
    mock_content.text = 'This section explains OAuth setup.'
    mock_message = mocker.Mock()
    mock_message.content = [mock_content]

    mock_client = mocker.Mock()
    mock_client.messages.create.return_value = mock_message
    mocker.patch('anthropic.Anthropic', return_value=mock_client)

    sections = [
        {'section_id': 'a', 'heading': 'OAuth', 'content': 'OAuth requires client credentials...'},
    ]

    summarizer = Summarizer(api_key='test-key')
    result = summarizer.summarize_sections(sections)

    assert result[0]['summary'] == 'This section explains OAuth setup.'


def test_summarizer_handles_claude_unavailable(mocker):
    """
    Given: Claude API is unavailable
    When: Attempting to summarize
    Then: Sections get summary=None without crashing
    """
    import anthropic

    from markdown_consolidator.summarizer import Summarizer

    mock_client = mocker.Mock()
    mock_client.messages.create.side_effect = anthropic.APIConnectionError(request=mocker.Mock())
    mocker.patch('anthropic.Anthropic', return_value=mock_client)

    sections = [{'section_id': 'a', 'heading': 'Test', 'content': 'Some content'}]

    summarizer = Summarizer(api_key='test-key')
    result = summarizer.summarize_sections(sections)

    assert result[0]['summary'] is None


def test_summarizer_handles_empty_input():
    """
    Given: Empty list of sections
    When: Summarizing
    Then: Returns empty list without error
    """
    from markdown_consolidator.summarizer import Summarizer

    summarizer = Summarizer()
    result = summarizer.summarize_sections([])

    assert result == []

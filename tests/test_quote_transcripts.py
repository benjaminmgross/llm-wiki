"""Tests for quote-anchor's transcripts mode (Phase 3).

In transcripts mode, ``_normalize`` strips ``[HH:MM:SS]`` timestamps and
``Speaker:`` line prefixes before comparing source and quote, so a quote
extracted mid-utterance still anchors against its source.
"""

from __future__ import annotations

import pytest

from mdwiki.quote import _normalize


@pytest.mark.unit
def test_normalize_default_mode_keeps_timestamps() -> None:
    """In default mode, [HH:MM:SS] tokens are part of the comparison."""
    text = "Sarah: [00:12:34] We should ship by Friday for sure."

    out = _normalize(text)

    # Default normalization collapses punctuation to spaces, but the digits
    # of the timestamp survive as words.
    assert "00" in out and "12" in out and "34" in out


@pytest.mark.unit
def test_normalize_transcripts_mode_strips_timestamps() -> None:
    """In transcripts mode, [HH:MM:SS] tokens are removed before comparison."""
    text = "Sarah: [00:12:34] We should ship by Friday for sure."

    out = _normalize(text, mode="transcripts")

    assert "00" not in out
    assert "12:34" not in out
    assert "we should ship by friday for sure" in out


@pytest.mark.unit
def test_normalize_transcripts_mode_strips_speaker_prefixes() -> None:
    """In transcripts mode, ``Speaker:`` line prefixes are removed."""
    text = "Sarah: We should ship by Friday for sure."

    out = _normalize(text, mode="transcripts")

    assert "sarah" not in out
    assert "we should ship by friday for sure" in out


@pytest.mark.unit
def test_quote_with_timestamp_in_source_anchors_in_transcripts_mode() -> None:
    """A quote that doesn't include the timestamp matches a source that does."""
    source = "Sarah: [00:12:34] We should ship by Friday for sure."
    quote = "We should ship by Friday for sure"

    norm_source = _normalize(source, mode="transcripts")
    norm_quote = _normalize(quote, mode="transcripts")

    assert norm_quote in norm_source


@pytest.mark.unit
def test_normalize_transcripts_mode_handles_hh_mm_only_timestamps() -> None:
    """Timestamp regex matches HH:MM as well as HH:MM:SS."""
    text = "[12:34] short timestamp variant here."

    out = _normalize(text, mode="transcripts")

    assert "12:34" not in out
    assert "short timestamp variant here" in out


@pytest.mark.unit
def test_normalize_default_mode_unchanged_for_existing_callers() -> None:
    """Calling ``_normalize`` without a mode kwarg behaves exactly as today."""
    # Regression check: the existing prose-normalization contract is intact.
    assert _normalize("Hello, world!") == "hello world"
    assert _normalize("**bold**") == "bold"
    assert _normalize("[link text](http://x.com)") == "link text"

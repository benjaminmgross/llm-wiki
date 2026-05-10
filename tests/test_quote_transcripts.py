"""Tests for quote-anchor's transcripts mode (Phase 3).

In transcripts mode, ``_normalize`` strips ``[HH:MM:SS]`` timestamps and
``Speaker:`` line prefixes before comparing source and quote, so a quote
extracted mid-utterance still anchors against its source.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from mdwiki.ingest import ingest_source
from mdwiki.init import init_wiki
from mdwiki.llm.base import CompleteResult
from mdwiki.plan import parse_plan
from mdwiki.quote import _normalize, quote_normalize_mode_for_wiki, verify_plan


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


@pytest.mark.unit
def test_quote_normalize_mode_for_wiki_default_when_no_config(tmp_path: Path) -> None:
    """No ``.mdwiki/config.toml`` → ``"default"``."""
    assert quote_normalize_mode_for_wiki(tmp_path) == "default"


@pytest.mark.unit
def test_quote_normalize_mode_for_wiki_default_when_profile_omits_mode(tmp_path: Path) -> None:
    """A profile without ``quote_normalize_mode`` declared → ``"default"``."""
    init_wiki(tmp_path, profile="framework")
    assert quote_normalize_mode_for_wiki(tmp_path) == "default"


@pytest.mark.unit
def test_quote_normalize_mode_for_wiki_reads_transcripts_profile(tmp_path: Path) -> None:
    """The transcripts profile sets ``quote_normalize_mode = "transcripts"``."""
    init_wiki(tmp_path, profile="transcripts")
    assert quote_normalize_mode_for_wiki(tmp_path) == "transcripts"


@pytest.mark.unit
def test_quote_normalize_mode_for_wiki_falls_back_on_unknown_value(tmp_path: Path) -> None:
    """An unrecognized declared mode falls back to ``"default"``."""
    init_wiki(tmp_path, profile="working-dir")
    config_path = tmp_path / ".mdwiki" / "config.toml"
    config_path.write_text(
        '[profile]\nname = "working-dir"\n\n[profile.working-dir]\nquote_normalize_mode = "garbage"\n'
    )
    assert quote_normalize_mode_for_wiki(tmp_path) == "default"


@pytest.mark.unit
def test_verify_plan_transcripts_mode_passes_quote_spanning_timestamp() -> None:
    """``verify_plan(mode="transcripts")`` accepts a quote whose words span the source's timestamp.

    In default mode the inline ``[HH:MM:SS]`` timestamp normalizes to digit-tokens
    that sit between the quoted words, so a verbatim quote that omits it does
    not anchor as a substring. In transcripts mode the timestamp is stripped
    from the source first, so the quote anchors.
    """
    # Timestamp sits MID-quote: "We should ship [00:12:34] by Friday".
    # Default mode normalizes the source to "... we should ship 00 12 34 by friday ...";
    # the quote "we should ship by friday for sure" is NOT a substring.
    # Transcripts mode strips the timestamp first, so the quote anchors.
    source_text = "**Sarah** [00:12:34]\nWe should ship [00:12:35] by Friday for sure.\n"
    section_ids = {"meeting.md/Discussion"}
    plan_json = json.dumps(
        {
            "verdict": "ingest",
            "rationale": "Captures the ship-date commitment.",
            "updates": [],
            "new_pages": [
                {
                    "path": "wiki/decisions/friday-ship.md",
                    "kind": "concept",
                    "content": "# Friday Ship Decision\n\nA commitment to ship by Friday.",
                    "claims": [
                        {
                            "source_section_id": "meeting.md/Discussion",
                            "quote": "We should ship by Friday for sure",
                        }
                    ],
                }
            ],
            "cross_refs": [],
        }
    )
    plan = parse_plan(plan_json)

    # Default mode rejects: timestamp digits sit between the quoted words.
    default_result = verify_plan(plan, source_text=source_text, section_ids=section_ids)
    assert default_result.valid is False

    # Transcripts mode accepts: timestamps are stripped from source first.
    transcripts_result = verify_plan(
        plan, source_text=source_text, section_ids=section_ids, mode="transcripts"
    )
    assert transcripts_result.valid is True


@pytest.mark.unit
def test_ingest_source_uses_transcripts_mode_for_transcripts_profile(
    tmp_path: Path,
    mocker: MockerFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: a transcripts-profile wiki ingests a quote that omits the timestamp.

    Regression for review C3: the transcripts profile's
    ``quote_normalize_mode = "transcripts"`` config flag must reach
    ``verify_plan`` so a verbatim quote without the source's ``[HH:MM:SS]``
    prefix passes verification.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    # The transcript loader (TranscriptLoader._convert_fathom_md) converts the
    # Fathom-block source into ``## Sarah [00:12:34]\n\n<utterance>``, which
    # the chunker then splits into a section keyed on that heading.
    (tmp_path / "fathom-meeting-transcript.md").write_text(
        "**Sarah** [00:12:34]\n"
        "We should ship [00:12:35] by Friday for sure to hit the launch window.\n"
    )
    init_wiki(tmp_path, profile="transcripts")

    plan_json = json.dumps(
        {
            "verdict": "ingest",
            "rationale": "Captures the Friday ship commitment.",
            "updates": [],
            "new_pages": [
                {
                    "path": "wiki/commitments/friday-ship.md",
                    "kind": "commitment",
                    "content": "# Friday Ship Commitment\n\nSarah commits to a Friday ship.",
                    "claims": [
                        {
                            "source_section_id": "fathom-meeting-transcript.md/Sarah [00:12:34]",
                            # Verbatim words but omitting the inline timestamp:
                            # the source contains "We should ship [00:12:35] by Friday ...".
                            # In default mode the timestamp digits sit between
                            # "ship" and "by" after normalization, so this quote
                            # would NOT anchor; in transcripts mode the timestamp
                            # is stripped first, so it does.
                            "quote": "We should ship by Friday for sure to hit the launch window",
                        }
                    ],
                }
            ],
            "cross_refs": [],
        }
    )
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.complete",
        return_value=CompleteResult(text=plan_json, input_tokens=100, output_tokens=50),
    )

    result = ingest_source(tmp_path, "fathom-meeting-transcript.md", yes=True)
    assert result.applied is True, f"transcripts-mode verification should pass: {result.message}"

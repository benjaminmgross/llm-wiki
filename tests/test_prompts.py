"""Tests for ``mdwiki.prompts`` — system prompt + user prompt builder for ingest."""

from __future__ import annotations

import pytest

from mdwiki.prompts import (
    INGEST_SYSTEM_PROMPT,
    INGEST_SYSTEM_PROMPT_HEAD,
    INGEST_SYSTEM_PROMPT_TOOL_USE,
    build_ingest_user_prompt,
)


@pytest.mark.unit
def test_system_prompt_includes_anti_sycophancy_license() -> None:
    prompt = INGEST_SYSTEM_PROMPT
    assert "skeptical" in prompt.lower()
    assert "empty" in prompt.lower() and "valid" in prompt.lower()
    assert "refuse" in prompt.lower()


@pytest.mark.unit
def test_system_prompt_describes_quote_anchor_contract() -> None:
    prompt = INGEST_SYSTEM_PROMPT
    assert "quote" in prompt.lower()
    assert "verbatim" in prompt.lower()
    assert "source_section_id" in prompt


@pytest.mark.unit
def test_system_prompt_specifies_json_schema() -> None:
    prompt = INGEST_SYSTEM_PROMPT
    assert "verdict" in prompt
    assert "updates" in prompt
    assert "new_pages" in prompt
    assert "cross_refs" in prompt


@pytest.mark.unit
def test_both_ingest_prompts_share_head_verbatim() -> None:
    """Guard that JSON-output and tool-use variants share an identical prose head.

    Both prompts are composed from ``INGEST_SYSTEM_PROMPT_HEAD`` plus their
    own tail (JSON spec vs tool-use directive). If a future edit drifts the
    head between variants — by editing only one composed string, or by
    inserting variant-specific guidance in the head — the two providers will
    silently diverge in behavior. Asserting both prompts start with the head
    verbatim makes the shared-head contract explicit.
    """
    assert INGEST_SYSTEM_PROMPT.startswith(INGEST_SYSTEM_PROMPT_HEAD)
    assert INGEST_SYSTEM_PROMPT_TOOL_USE.startswith(INGEST_SYSTEM_PROMPT_HEAD)


@pytest.mark.unit
def test_user_prompt_includes_source_with_section_ids() -> None:
    prompt = build_ingest_user_prompt(
        source_path="ai/foo.md",
        sections=[
            {
                "section_id": "foo.md/Intro",
                "heading": "Intro",
                "content": "Some intro text with enough words for quoting.",
            },
            {
                "section_id": "foo.md/Body",
                "heading": "Body",
                "content": "Body content with enough words for quoting.",
            },
        ],
        candidate_pages=[],
        schema_text="<schema content>",
        recent_log_entries=[],
    )
    assert "ai/foo.md" in prompt
    assert "foo.md/Intro" in prompt
    assert "foo.md/Body" in prompt
    assert "Some intro text with enough words for quoting." in prompt
    assert "<schema content>" in prompt


@pytest.mark.unit
def test_user_prompt_includes_verified_quote_bank() -> None:
    prompt = build_ingest_user_prompt(
        source_path="ai/foo.md",
        sections=[
            {
                "section_id": "foo.md/Intro",
                "heading": "Intro",
                "content": "- **Exact source bullet:** copy this quote without paraphrasing\n```mermaid\nskip this",
            },
        ],
        candidate_pages=[],
        schema_text="schema",
        recent_log_entries=[],
    )
    assert "Verified quote bank" in prompt
    assert "section_id: foo.md/Intro" in prompt
    assert "quote: - **Exact source bullet:** copy this quote without paraphrasing" in prompt
    assert "quote: ```mermaid" not in prompt


@pytest.mark.unit
def test_user_prompt_includes_candidate_pages_when_present() -> None:
    prompt = build_ingest_user_prompt(
        source_path="ai/foo.md",
        sections=[{"section_id": "foo.md/Intro", "heading": "Intro", "content": "x"}],
        candidate_pages=[
            {"path": "wiki/concepts/attention.md", "content": "## Existing\n\nattention discussion"}
        ],
        schema_text="schema",
        recent_log_entries=[],
    )
    assert "wiki/concepts/attention.md" in prompt
    assert "attention discussion" in prompt


@pytest.mark.unit
def test_user_prompt_handles_no_candidates_gracefully() -> None:
    prompt = build_ingest_user_prompt(
        source_path="ai/foo.md",
        sections=[{"section_id": "foo.md/x", "heading": "x", "content": "x"}],
        candidate_pages=[],
        schema_text="schema",
        recent_log_entries=[],
    )
    assert "no candidate" in prompt.lower() or "(none)" in prompt.lower() or "0 candidate" in prompt.lower()


@pytest.mark.unit
def test_user_prompt_includes_recent_log_entries_when_present() -> None:
    prompt = build_ingest_user_prompt(
        source_path="ai/foo.md",
        sections=[{"section_id": "foo.md/x", "heading": "x", "content": "x"}],
        candidate_pages=[],
        schema_text="schema",
        recent_log_entries=["2026-04-29: ingested attention-paper.md → 2 pages touched"],
    )
    assert "attention-paper.md" in prompt


@pytest.mark.unit
def test_user_prompt_handles_empty_sections_list() -> None:
    """A source with no extractable sections (no H2 headers) still yields a prompt."""
    prompt = build_ingest_user_prompt(
        source_path="ai/notes.md",
        sections=[],
        candidate_pages=[],
        schema_text="schema",
        recent_log_entries=[],
    )
    assert "ai/notes.md" in prompt

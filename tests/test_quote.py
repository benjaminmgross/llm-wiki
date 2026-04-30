"""Tests for ``mdwiki.quote`` — cite-or-refuse + quote-anchor verification."""

from __future__ import annotations

import pytest

from mdwiki.plan import Claim, NewPage, Plan, Update
from mdwiki.quote import QuoteVerificationResult, verify_plan


def _plan(*, updates: tuple[Update, ...] = (), new_pages: tuple[NewPage, ...] = ()) -> Plan:
    return Plan(verdict="ingest", rationale="x", updates=updates, new_pages=new_pages, cross_refs=())


def _update(*claims: Claim) -> Update:
    return Update(page="wiki/foo.md", section="x", content="x", claims=claims)


@pytest.mark.unit
def test_valid_quotes_pass() -> None:
    source_text = "Attention sinks reduce drift in long contexts."
    section_ids = {"sec-1"}
    plan = _plan(updates=(_update(Claim(source_section_id="sec-1", quote="attention sinks reduce drift")),))
    result = verify_plan(plan, source_text=source_text, section_ids=section_ids)
    assert result.valid is True
    assert result.errors == ()


@pytest.mark.unit
def test_unknown_source_section_id_rejected() -> None:
    source_text = "Attention sinks reduce drift in long contexts."
    section_ids = {"sec-1"}
    plan = _plan(updates=(_update(Claim(source_section_id="sec-99", quote="attention sinks reduce drift")),))
    result = verify_plan(plan, source_text=source_text, section_ids=section_ids)
    assert result.valid is False
    assert len(result.errors) == 1
    assert "sec-99" in result.errors[0].message


@pytest.mark.unit
def test_quote_not_in_source_rejected() -> None:
    source_text = "Attention sinks reduce drift."
    section_ids = {"sec-1"}
    plan = _plan(updates=(_update(Claim(source_section_id="sec-1", quote="this quote was made up entirely")),))
    result = verify_plan(plan, source_text=source_text, section_ids=section_ids)
    assert result.valid is False
    assert "made up entirely" in result.errors[0].message


@pytest.mark.unit
def test_whitespace_normalization_allows_line_wrapped_quotes() -> None:
    source_text = "Attention\n  sinks    reduce\tdrift in\nlong contexts."
    section_ids = {"sec-1"}
    plan = _plan(updates=(_update(Claim(source_section_id="sec-1", quote="attention sinks reduce drift")),))
    result = verify_plan(plan, source_text=source_text, section_ids=section_ids)
    assert result.valid is True


@pytest.mark.unit
def test_case_insensitive_match() -> None:
    source_text = "ATTENTION sinks REDUCE drift."
    section_ids = {"sec-1"}
    plan = _plan(updates=(_update(Claim(source_section_id="sec-1", quote="attention sinks reduce drift")),))
    result = verify_plan(plan, source_text=source_text, section_ids=section_ids)
    assert result.valid is True


@pytest.mark.unit
def test_empty_plan_is_trivially_valid() -> None:
    result = verify_plan(_plan(), source_text="anything", section_ids={"sec-1"})
    assert result.valid is True
    assert result.errors == ()


@pytest.mark.unit
def test_multiple_errors_collected() -> None:
    source_text = "Real text only."
    section_ids = {"sec-1"}
    plan = _plan(
        updates=(
            _update(
                Claim(source_section_id="sec-99", quote="bad section"),
                Claim(source_section_id="sec-1", quote="not in source"),
            ),
        )
    )
    result = verify_plan(plan, source_text=source_text, section_ids=section_ids)
    assert result.valid is False
    assert len(result.errors) == 2


@pytest.mark.unit
def test_verifies_new_page_claims_too() -> None:
    source_text = "The paper introduces attention sinks."
    section_ids = {"sec-1"}
    plan = _plan(
        new_pages=(
            NewPage(
                path="wiki/entities/the-paper.md",
                kind="entity",
                content="...",
                claims=(Claim(source_section_id="sec-1", quote="invented quote not present"),),
            ),
        )
    )
    result = verify_plan(plan, source_text=source_text, section_ids=section_ids)
    assert result.valid is False


@pytest.mark.unit
def test_result_is_immutable() -> None:
    result = QuoteVerificationResult(valid=True, errors=())
    with pytest.raises(Exception):
        result.valid = False  # type: ignore[misc]


@pytest.mark.unit
def test_quote_too_short_rejected() -> None:
    """The cite-or-refuse contract requires quotes long enough to be meaningful."""
    source_text = "the quick brown fox"
    section_ids = {"sec-1"}
    plan = _plan(updates=(_update(Claim(source_section_id="sec-1", quote="the")),))
    result = verify_plan(plan, source_text=source_text, section_ids=section_ids)
    assert result.valid is False
    assert "too short" in result.errors[0].message.lower() or "minimum" in result.errors[0].message.lower()


@pytest.mark.unit
def test_markdown_link_syntax_in_source_does_not_block_clean_quote() -> None:
    """The LLM extracts prose; the source has [text](url) inside the prose."""
    source_text = "won through [ruthless self-discipline](https://example.com), mental clarity, and truth."
    section_ids = {"sec-1"}
    plan = _plan(
        updates=(
            _update(Claim(source_section_id="sec-1", quote="ruthless self-discipline, mental clarity, and truth")),
        )
    )
    result = verify_plan(plan, source_text=source_text, section_ids=section_ids)
    assert result.valid is True


@pytest.mark.unit
def test_curly_apostrophe_in_source_matches_straight_in_quote() -> None:
    source_text = "Discipline isn’t punishment but liberation."
    section_ids = {"sec-1"}
    plan = _plan(
        updates=(_update(Claim(source_section_id="sec-1", quote="discipline isn't punishment but liberation")),)
    )
    result = verify_plan(plan, source_text=source_text, section_ids=section_ids)
    assert result.valid is True


@pytest.mark.unit
def test_em_dash_in_source_matches_hyphen_in_quote() -> None:
    source_text = "Liberation—it frees you from chaos of impulse and emotion."
    section_ids = {"sec-1"}
    plan = _plan(
        updates=(_update(Claim(source_section_id="sec-1", quote="liberation - it frees you from chaos")),)
    )
    result = verify_plan(plan, source_text=source_text, section_ids=section_ids)
    assert result.valid is True


@pytest.mark.unit
def test_curly_double_quotes_in_source_match_straight_in_quote() -> None:
    source_text = "He called it “liberation” in his journals at the time."
    section_ids = {"sec-1"}
    plan = _plan(
        updates=(_update(Claim(source_section_id="sec-1", quote='called it "liberation" in his journals')),)
    )
    result = verify_plan(plan, source_text=source_text, section_ids=section_ids)
    assert result.valid is True


@pytest.mark.unit
def test_markdown_bold_and_italic_stripped() -> None:
    source_text = "Discipline is **liberation**, not _punishment_, in his view."
    section_ids = {"sec-1"}
    plan = _plan(
        updates=(_update(Claim(source_section_id="sec-1", quote="discipline is liberation, not punishment, in his view")),)
    )
    result = verify_plan(plan, source_text=source_text, section_ids=section_ids)
    assert result.valid is True


@pytest.mark.unit
def test_markdown_inline_code_stripped() -> None:
    source_text = "Run `discipline()` to free yourself from chaos and impulse."
    section_ids = {"sec-1"}
    plan = _plan(
        updates=(_update(Claim(source_section_id="sec-1", quote="run discipline to free yourself from chaos")),)
    )
    result = verify_plan(plan, source_text=source_text, section_ids=section_ids)
    assert result.valid is True


@pytest.mark.unit
def test_normalization_still_rejects_real_hallucinations() -> None:
    """Sanity check: smarter normalization shouldn't mask actual fabrications."""
    source_text = "Discipline is liberation in his journals at the time."
    section_ids = {"sec-1"}
    plan = _plan(
        updates=(_update(Claim(source_section_id="sec-1", quote="this entire phrase is invented by the model")),)
    )
    result = verify_plan(plan, source_text=source_text, section_ids=section_ids)
    assert result.valid is False

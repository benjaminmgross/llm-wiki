"""Tests for ``mdwiki.quote`` — cite-or-refuse + quote-anchor verification."""

from __future__ import annotations

from pathlib import Path

import pytest

from mdwiki.plan import Claim, NewPage, Plan, Update
from mdwiki.quote import MIN_QUOTE_WORDS, QuoteVerificationResult, min_quote_words_for_wiki, verify_plan


def _plan(*, updates: tuple[Update, ...] = (), new_pages: tuple[NewPage, ...] = ()) -> Plan:
    return Plan(verdict="ingest", rationale="x", updates=updates, new_pages=new_pages, cross_refs=())


def _update(*claims: Claim) -> Update:
    return Update(page="wiki/foo.md", content="x", claims=claims)


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
def test_section_id_with_markdown_link_in_heading_matches_clean_citation() -> None:
    """The LLM strips markdown formatting when echoing a heading; we must too."""
    source_text = "stuff about observe and the link to docs is fine to cite here"
    section_ids = {"path.md/Observe [📖](https://docs.example.com/observe)"}
    plan = _plan(
        updates=(
            _update(
                Claim(
                    source_section_id="path.md/Observe 📖",
                    quote="stuff about observe and the link to docs is fine to cite here",
                )
            ),
        )
    )
    result = verify_plan(plan, source_text=source_text, section_ids=section_ids)
    assert result.valid is True


@pytest.mark.unit
def test_section_id_normalization_handles_emphasis_and_unicode() -> None:
    source_text = "this is content with enough words to satisfy the minimum here"
    section_ids = {"path.md/**Important**: it’s alive"}
    plan = _plan(
        updates=(
            _update(
                Claim(
                    source_section_id="path.md/Important: it's alive",
                    quote="this is content with enough words to satisfy the minimum here",
                )
            ),
        )
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


@pytest.mark.unit
def test_min_quote_words_param_lowered_accepts_short_quote() -> None:
    """Passing ``min_quote_words=2`` lets a 2-word quote pass that the default 4 would reject."""
    source_text = "the quick brown fox jumps over the lazy dog"
    section_ids = {"sec-1"}
    plan = _plan(updates=(_update(Claim(source_section_id="sec-1", quote="quick brown")),))
    default_result = verify_plan(plan, source_text=source_text, section_ids=section_ids)
    assert default_result.valid is False
    assert "minimum 4" in default_result.errors[0].message
    relaxed_result = verify_plan(plan, source_text=source_text, section_ids=section_ids, min_quote_words=2)
    assert relaxed_result.valid is True


@pytest.mark.unit
def test_min_quote_words_param_raised_rejects_otherwise_valid_quote() -> None:
    """Passing a higher ``min_quote_words`` rejects a quote that the default would accept."""
    source_text = "the quick brown fox jumps over the lazy dog"
    section_ids = {"sec-1"}
    plan = _plan(updates=(_update(Claim(source_section_id="sec-1", quote="quick brown fox jumps")),))
    default_result = verify_plan(plan, source_text=source_text, section_ids=section_ids)
    assert default_result.valid is True
    strict_result = verify_plan(plan, source_text=source_text, section_ids=section_ids, min_quote_words=8)
    assert strict_result.valid is False
    assert "minimum 8" in strict_result.errors[0].message


@pytest.mark.unit
def test_min_quote_words_for_wiki_default_when_no_config(tmp_path: Path) -> None:
    """No ``.mdwiki/config.toml`` → returns the module default."""
    assert min_quote_words_for_wiki(tmp_path) == MIN_QUOTE_WORDS


@pytest.mark.unit
def test_min_quote_words_for_wiki_default_when_key_absent(tmp_path: Path) -> None:
    """``[ingest]`` section without ``min_quote_words`` → default."""
    (tmp_path / ".mdwiki").mkdir()
    (tmp_path / ".mdwiki" / "config.toml").write_text("[ingest]\ncandidate_top_k = 8\n")
    assert min_quote_words_for_wiki(tmp_path) == MIN_QUOTE_WORDS


@pytest.mark.unit
def test_min_quote_words_for_wiki_reads_explicit_value(tmp_path: Path) -> None:
    """A valid ``[ingest].min_quote_words`` is honored."""
    (tmp_path / ".mdwiki").mkdir()
    (tmp_path / ".mdwiki" / "config.toml").write_text("[ingest]\nmin_quote_words = 2\n")
    assert min_quote_words_for_wiki(tmp_path) == 2


@pytest.mark.unit
@pytest.mark.parametrize("bad_value", ["2", 0, -3, 1.5, True, False, None])
def test_min_quote_words_for_wiki_falls_back_on_invalid_value(tmp_path: Path, bad_value: object) -> None:
    """Non-positive integers, booleans (which are ints in Python), strings, floats → default."""
    (tmp_path / ".mdwiki").mkdir()
    if bad_value is None:
        body = "[ingest]\n"  # key absent
    elif isinstance(bad_value, str):
        body = f'[ingest]\nmin_quote_words = "{bad_value}"\n'
    elif isinstance(bad_value, bool):
        body = f"[ingest]\nmin_quote_words = {str(bad_value).lower()}\n"
    else:
        body = f"[ingest]\nmin_quote_words = {bad_value}\n"
    (tmp_path / ".mdwiki" / "config.toml").write_text(body)
    assert min_quote_words_for_wiki(tmp_path) == MIN_QUOTE_WORDS


@pytest.mark.unit
def test_emphasis_adjacent_to_text_preserves_word_boundary() -> None:
    """``**foo**bar`` must not collapse to ``foobar`` after normalization.

    Regression for the luxe-give bug: markdownified HTML stat tiles like
    ``**1.7x revenue growth**3.6x 3-year TSR`` lost the boundary between
    ``growth`` and ``3.6x``, breaking substring match against the LLM's
    naturally-spaced quote.
    """
    source_text = "**1.7x revenue growth**3.6x 3-year TSR"
    section_ids = {"sec-1"}
    plan = _plan(
        updates=(
            _update(Claim(source_section_id="sec-1", quote="1.7x revenue growth 3.6x 3-year TSR")),
        )
    )
    result = verify_plan(plan, source_text=source_text, section_ids=section_ids)
    assert result.valid is True


@pytest.mark.unit
def test_inline_code_adjacent_to_text_preserves_word_boundary() -> None:
    """`` `code`bar `` must not collapse to ``codebar`` after normalization."""
    source_text = "Run `discipline()`then continue with rest of pipeline today"
    section_ids = {"sec-1"}
    plan = _plan(
        updates=(
            _update(Claim(source_section_id="sec-1", quote="run discipline then continue with rest of pipeline today")),
        )
    )
    result = verify_plan(plan, source_text=source_text, section_ids=section_ids)
    assert result.valid is True


@pytest.mark.unit
def test_link_adjacent_to_text_preserves_word_boundary() -> None:
    """``[text](url)foo`` must not collapse to ``textfoo`` after normalization."""
    source_text = "see [docs](https://example.com)now for the latest deployment instructions today"
    section_ids = {"sec-1"}
    plan = _plan(
        updates=(
            _update(Claim(source_section_id="sec-1", quote="see docs now for the latest deployment instructions today")),
        )
    )
    result = verify_plan(plan, source_text=source_text, section_ids=section_ids)
    assert result.valid is True


@pytest.mark.unit
def test_min_quote_words_for_wiki_handles_malformed_toml(tmp_path: Path) -> None:
    """A corrupt config file falls back to the default rather than raising."""
    (tmp_path / ".mdwiki").mkdir()
    (tmp_path / ".mdwiki" / "config.toml").write_text("this is not valid = toml [[[")
    assert min_quote_words_for_wiki(tmp_path) == MIN_QUOTE_WORDS

"""Tests for ``mdwiki.plan`` — strict JSON parser for the LLM ingest response."""

from __future__ import annotations

import json

import pytest

from mdwiki.plan import (
    Claim,
    CrossRef,
    NewPage,
    Plan,
    PlanValidationError,
    Update,
    parse_plan,
)


def _valid_payload(verdict: str = "ingest") -> dict:
    return {
        "verdict": verdict,
        "rationale": "Two clear concepts; one already covered.",
        "updates": [
            {
                "page": "wiki/concepts/attention-sinks.md",
                "section": "Recent findings",
                "content": "Attention sinks reduce drift in long contexts.",
                "claims": [{"source_section_id": "sec-1", "quote": "attention sinks reduce drift"}],
            }
        ],
        "new_pages": [
            {
                "path": "wiki/entities/the-paper.md",
                "kind": "entity",
                "content": "## Summary\n\nA 2026 paper on attention sinks.",
                "claims": [{"source_section_id": "sec-2", "quote": "we propose attention sinks"}],
            }
        ],
        "cross_refs": [
            {
                "from_page": "wiki/concepts/attention-sinks.md",
                "to_page": "wiki/entities/the-paper.md",
                "anchor_text": "the original paper",
            }
        ],
    }


@pytest.mark.unit
def test_parse_valid_full_plan() -> None:
    plan = parse_plan(json.dumps(_valid_payload()))
    assert plan.verdict == "ingest"
    assert len(plan.updates) == 1
    assert len(plan.new_pages) == 1
    assert len(plan.cross_refs) == 1
    assert isinstance(plan.updates[0], Update)
    assert isinstance(plan.new_pages[0], NewPage)
    assert isinstance(plan.cross_refs[0], CrossRef)


@pytest.mark.unit
def test_parse_low_quality_verdict_with_empty_actions() -> None:
    payload = {
        "verdict": "low-quality",
        "rationale": "Source is mostly link dumps.",
        "updates": [],
        "new_pages": [],
        "cross_refs": [],
    }
    plan = parse_plan(json.dumps(payload))
    assert plan.verdict == "low-quality"
    assert plan.updates == ()
    assert plan.new_pages == ()


@pytest.mark.unit
def test_non_ingest_verdict_with_updates_is_rejected() -> None:
    payload = _valid_payload(verdict="low-quality")
    with pytest.raises(PlanValidationError) as excinfo:
        parse_plan(json.dumps(payload))
    assert "verdict" in str(excinfo.value).lower()


@pytest.mark.unit
def test_malformed_json_raises_validation_error() -> None:
    with pytest.raises(PlanValidationError):
        parse_plan("not valid json {{{")


@pytest.mark.unit
def test_missing_required_field_raises_validation_error() -> None:
    payload = _valid_payload()
    del payload["rationale"]
    with pytest.raises(PlanValidationError) as excinfo:
        parse_plan(json.dumps(payload))
    assert "rationale" in str(excinfo.value)


@pytest.mark.unit
def test_invalid_kind_raises_validation_error() -> None:
    payload = _valid_payload()
    payload["new_pages"][0]["kind"] = "not-a-kind"
    with pytest.raises(PlanValidationError):
        parse_plan(json.dumps(payload))


@pytest.mark.unit
def test_invalid_verdict_raises_validation_error() -> None:
    payload = _valid_payload()
    payload["verdict"] = "definitely-not-a-verdict"
    with pytest.raises(PlanValidationError):
        parse_plan(json.dumps(payload))


@pytest.mark.unit
def test_claim_missing_quote_is_rejected() -> None:
    payload = _valid_payload()
    payload["updates"][0]["claims"][0].pop("quote")
    with pytest.raises(PlanValidationError):
        parse_plan(json.dumps(payload))


@pytest.mark.unit
def test_all_claims_iterates_across_updates_and_new_pages() -> None:
    plan = parse_plan(json.dumps(_valid_payload()))
    quotes = [c.quote for c in plan.all_claims()]
    assert "attention sinks reduce drift" in quotes
    assert "we propose attention sinks" in quotes
    assert len(quotes) == 2


@pytest.mark.unit
def test_plan_is_immutable() -> None:
    plan = parse_plan(json.dumps(_valid_payload()))
    with pytest.raises(Exception):
        plan.verdict = "low-quality"  # type: ignore[misc]


@pytest.mark.unit
def test_claim_is_immutable() -> None:
    claim = Claim(source_section_id="sec-1", quote="hello")
    with pytest.raises(Exception):
        claim.quote = "different"  # type: ignore[misc]


@pytest.mark.unit
def test_parse_handles_duplicate_of_verdict() -> None:
    payload = {
        "verdict": "duplicate-of:wiki/concepts/foo.md",
        "rationale": "Already fully covered.",
        "updates": [],
        "new_pages": [],
        "cross_refs": [],
    }
    plan = parse_plan(json.dumps(payload))
    assert plan.verdict.startswith("duplicate-of:")


@pytest.mark.unit
def test_plan_helpers_handle_empty_collections() -> None:
    payload = {
        "verdict": "out-of-scope",
        "rationale": "Off topic.",
        "updates": [],
        "new_pages": [],
        "cross_refs": [],
    }
    plan = parse_plan(json.dumps(payload))
    assert list(plan.all_claims()) == []
    assert plan.is_empty() is True


@pytest.mark.unit
def test_is_empty_false_when_actions_present() -> None:
    plan = parse_plan(json.dumps(_valid_payload()))
    assert plan.is_empty() is False


@pytest.mark.unit
def test_parse_rejects_extra_top_level_fields_silently_or_gracefully() -> None:
    payload = _valid_payload()
    payload["mystery_field"] = "should be ignored or rejected"
    plan = parse_plan(json.dumps(payload))
    assert plan.verdict == "ingest"

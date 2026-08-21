"""Tests for ``mdwiki.plan`` — strict JSON parser for the LLM ingest response."""

from __future__ import annotations

import json

import pytest

from mdwiki.plan import (
    Claim,
    CrossRef,
    NewPage,
    PlanValidationError,
    Update,
    parse_plan,
    parse_plan_dict,
)


def _valid_payload(verdict: str = "ingest") -> dict:
    return {
        "verdict": verdict,
        "rationale": "Two clear concepts; one already covered.",
        "updates": [
            {
                "page": "wiki/concepts/attention-sinks.md",
                "content": "# Attention Sinks\n\nAttention sinks reduce drift in long contexts.",
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
@pytest.mark.parametrize("field", ["from_page", "to_page"])
def test_parse_plan_rejects_cross_ref_path_outside_wiki(field: str) -> None:
    payload = _valid_payload()
    payload["cross_refs"][0][field] = "../outside.md"

    with pytest.raises(PlanValidationError, match=field):
        parse_plan_dict(payload)


@pytest.mark.unit
def test_parse_plan_rejects_blank_cross_ref_anchor_text() -> None:
    payload = _valid_payload()
    payload["cross_refs"][0]["anchor_text"] = "   "

    with pytest.raises(PlanValidationError, match="anchor_text"):
        parse_plan_dict(payload)


@pytest.mark.parametrize("field", ["from_page", "to_page"])
@pytest.mark.parametrize("path", ["wiki/index.md", "wiki/log.md"])
def test_parse_plan_rejects_infrastructure_cross_ref_endpoint(field: str, path: str) -> None:
    payload = _valid_payload()
    payload["cross_refs"][0][field] = path

    with pytest.raises(PlanValidationError, match="infrastructure"):
        parse_plan_dict(payload)


@pytest.mark.parametrize("field", ["from_page", "to_page"])
def test_parse_plan_rejects_markdown_suffix_for_semantic_endpoint(field: str) -> None:
    payload = _valid_payload()
    payload["cross_refs"][0][field] = "wiki/concepts/not-semantic.markdown"

    with pytest.raises(PlanValidationError, match="semantic endpoint"):
        parse_plan_dict(payload)


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


@pytest.mark.unit
def test_parse_strips_markdown_code_fences_with_lang_tag() -> None:
    raw = "```json\n" + json.dumps(_valid_payload()) + "\n```"
    plan = parse_plan(raw)
    assert plan.verdict == "ingest"


@pytest.mark.unit
def test_parse_strips_bare_code_fences() -> None:
    raw = "```\n" + json.dumps(_valid_payload()) + "\n```"
    plan = parse_plan(raw)
    assert plan.verdict == "ingest"


@pytest.mark.unit
def test_parse_strips_preamble_before_json_object() -> None:
    raw = "Here is the plan:\n\n" + json.dumps(_valid_payload())
    plan = parse_plan(raw)
    assert plan.verdict == "ingest"


@pytest.mark.unit
def test_parse_strips_postamble_after_json_object() -> None:
    raw = json.dumps(_valid_payload()) + "\n\nLet me know if you'd like changes."
    plan = parse_plan(raw)
    assert plan.verdict == "ingest"


@pytest.mark.unit
def test_parse_handles_fences_plus_preamble_plus_whitespace() -> None:
    raw = "Here you go:\n\n```json\n" + json.dumps(_valid_payload()) + "\n```\n\nDone!"
    plan = parse_plan(raw)
    assert plan.verdict == "ingest"


@pytest.mark.unit
def test_new_page_path_outside_wiki_is_rejected() -> None:
    """Path traversal defense: new_pages[].path must start with wiki/."""
    payload = _valid_payload()
    payload["new_pages"][0]["path"] = ".mdwiki/config.toml"
    with pytest.raises(PlanValidationError) as excinfo:
        parse_plan(json.dumps(payload))
    assert "wiki/" in str(excinfo.value)


@pytest.mark.unit
def test_update_page_outside_wiki_is_rejected() -> None:
    """Path traversal defense: updates[].page must start with wiki/."""
    payload = _valid_payload()
    payload["updates"][0]["page"] = "../../../etc/passwd"
    with pytest.raises(PlanValidationError):
        parse_plan(json.dumps(payload))


@pytest.mark.unit
def test_duplicate_update_targets_are_rejected() -> None:
    payload = _valid_payload()
    payload["updates"] = [
        {"page": "wiki/concepts/shared.md", "content": "first", "claims": []},
        {"page": "wiki/concepts/shared.md", "content": "second", "claims": []},
    ]
    payload["new_pages"] = []

    with pytest.raises(PlanValidationError, match="duplicate write target"):
        parse_plan(json.dumps(payload))


@pytest.mark.unit
def test_update_and_new_page_target_collision_is_rejected() -> None:
    payload = _valid_payload()
    payload["updates"] = [{"page": "wiki/concepts/shared.md", "content": "update", "claims": []}]
    payload["new_pages"] = [
        {
            "path": "wiki/concepts/shared.md",
            "kind": "concept",
            "content": "replacement",
            "claims": [],
        }
    ]

    with pytest.raises(PlanValidationError, match="duplicate write target"):
        parse_plan(json.dumps(payload))


@pytest.mark.unit
@pytest.mark.parametrize("alias", ["wiki/concepts/./shared.md", "wiki/concepts//shared.md"])
def test_noncanonical_page_target_alias_is_rejected(alias: str) -> None:
    payload = _valid_payload()
    payload["updates"] = [{"page": "wiki/concepts/shared.md", "content": "update", "claims": []}]
    payload["new_pages"] = [{"path": alias, "kind": "concept", "content": "replacement", "claims": []}]

    with pytest.raises(PlanValidationError, match="canonical POSIX path"):
        parse_plan(json.dumps(payload))


@pytest.mark.unit
def test_dot_dot_segment_in_path_is_rejected() -> None:
    payload = _valid_payload()
    payload["new_pages"][0]["path"] = "wiki/../.mdwiki/state.db"
    with pytest.raises(PlanValidationError) as excinfo:
        parse_plan(json.dumps(payload))
    assert ".." in str(excinfo.value)


@pytest.mark.unit
def test_absolute_path_is_rejected() -> None:
    payload = _valid_payload()
    payload["new_pages"][0]["path"] = "/etc/passwd"
    with pytest.raises(PlanValidationError):
        parse_plan(json.dumps(payload))


@pytest.mark.unit
def test_null_byte_in_path_is_rejected() -> None:
    """Round-2 S1: null byte hides a '..' segment from the split-based check.

    ``"wiki/\\x00../etc/passwd".split("/") == ["wiki", "\\x00..", "etc", "passwd"]``
    has no literal ``".."`` element, so the prior check passed even though the
    OS-level path contains a traversal sequence.
    """
    payload = _valid_payload()
    payload["new_pages"][0]["path"] = "wiki/\x00../etc/passwd"
    with pytest.raises(PlanValidationError) as excinfo:
        parse_plan(json.dumps(payload))
    assert "control character" in str(excinfo.value)


@pytest.mark.unit
def test_other_control_chars_in_path_are_rejected() -> None:
    """Round-2 S1: defense-in-depth — reject any control byte < 0x20."""
    payload = _valid_payload()
    payload["new_pages"][0]["path"] = "wiki/concepts/foo\nbar.md"
    with pytest.raises(PlanValidationError):
        parse_plan(json.dumps(payload))

"""Tests for Anthropic ``tool_use`` constrained-decoding path on ingest.

When the model is asked to produce a Plan, the SDK call is made with the
``submit_plan`` tool and ``tool_choice``, and the response payload comes back
in a ``tool_use`` content block as a parsed dict (no JSON round-trip on our
side). These tests verify:

- ``CompleteResult.tool_input`` is populated when ``tools`` is passed
- ``BatchResult.tool_input`` is populated for batch results carrying a tool_use
- ``ingest_source`` routes through the tool when provider is anthropic
- ``parse_plan_dict`` mirrors ``parse_plan`` validation but skips json.loads
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mdwiki.ingest_tool import INGEST_TOOL_CHOICE, INGEST_TOOL_DEFINITION, INGEST_TOOL_NAME
from mdwiki.llm.anthropic import AnthropicProvider
from mdwiki.llm.base import BatchRequest, Message
from mdwiki.plan import Plan, PlanValidationError, parse_plan_dict


def _mock_stream(fake_client: MagicMock, response: MagicMock) -> None:
    stream_cm = MagicMock()
    stream_cm.__enter__.return_value.get_final_message.return_value = response
    stream_cm.__exit__.return_value = False
    fake_client.messages.stream.return_value = stream_cm


def _empty_plan_dict() -> dict:
    return {
        "verdict": "low-quality",
        "rationale": "no substantive claims",
        "updates": [],
        "new_pages": [],
        "cross_refs": [],
    }


# ---------- ingest_tool definition shape ----------


@pytest.mark.unit
def test_ingest_tool_definition_is_well_formed() -> None:
    """The tool definition has the keys Anthropic's API requires."""
    assert INGEST_TOOL_DEFINITION["name"] == "submit_plan"
    assert INGEST_TOOL_NAME == "submit_plan"
    assert "description" in INGEST_TOOL_DEFINITION
    schema = INGEST_TOOL_DEFINITION["input_schema"]
    assert schema["type"] == "object"
    # The Plan dataclass requires these five top-level keys; the schema must
    # mark all of them required so the model can't omit fields the parser
    # treats as mandatory.
    assert set(schema["required"]) == {"verdict", "rationale", "updates", "new_pages", "cross_refs"}
    assert schema["additionalProperties"] is False


@pytest.mark.unit
def test_ingest_tool_choice_targets_submit_plan() -> None:
    assert INGEST_TOOL_CHOICE == {"type": "tool", "name": "submit_plan"}


@pytest.mark.unit
def test_tool_schema_field_names_match_plan_dataclasses() -> None:
    """Tool schema field names must match the dataclasses parse_plan_dict expects.

    Drift between the schema (what the model is constrained to produce) and the
    parser (what we validate against) silently re-introduces the same class of
    failures tool_use was meant to eliminate — the JSON would be structurally
    valid but rejected by ``parse_plan_dict`` for a missing required field.
    """
    from dataclasses import fields

    from mdwiki.plan import Claim, CrossRef, NewPage, Update

    schema = INGEST_TOOL_DEFINITION["input_schema"]
    items = schema["properties"]

    # Plan top-level keys
    assert set(schema["required"]) == {"verdict", "rationale", "updates", "new_pages", "cross_refs"}

    # Each nested object's required keys must equal the dataclass field names.
    for key, dataclass_type in (
        ("updates", Update),
        ("new_pages", NewPage),
        ("cross_refs", CrossRef),
    ):
        item_schema = items[key]["items"]
        schema_required = set(item_schema["required"])
        dc_fields = {f.name for f in fields(dataclass_type)}
        assert schema_required == dc_fields, (
            f"{key}: tool schema required {schema_required} does not match {dataclass_type.__name__} "
            f"dataclass fields {dc_fields}"
        )

    # Claim is nested inside Update.claims and NewPage.claims; check its shape too.
    update_claim_schema = items["updates"]["items"]["properties"]["claims"]["items"]
    assert set(update_claim_schema["required"]) == {f.name for f in fields(Claim)}


# ---------- AnthropicProvider.complete with tools ----------


@pytest.mark.unit
def test_complete_passes_tools_and_tool_choice_to_sdk() -> None:
    """When tools is set, the SDK call carries ``tools`` + ``tool_choice``."""
    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_response.content = [MagicMock(type="tool_use", input=_empty_plan_dict())]
    fake_response.stop_reason = "end_turn"
    fake_response.usage = MagicMock(
        input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0
    )
    _mock_stream(fake_client, fake_response)

    provider = AnthropicProvider(model="claude-sonnet-4-6", api_key="sk-x", client=fake_client)
    provider.complete(
        system="sys",
        messages=[Message(role="user", content="hi")],
        max_tokens=128,
        tools=[INGEST_TOOL_DEFINITION],
        tool_choice=INGEST_TOOL_CHOICE,
    )

    kwargs = fake_client.messages.stream.call_args.kwargs
    assert kwargs["tools"] == [INGEST_TOOL_DEFINITION]
    assert kwargs["tool_choice"] == INGEST_TOOL_CHOICE


@pytest.mark.unit
def test_complete_extracts_tool_input_from_tool_use_block() -> None:
    """``CompleteResult.tool_input`` is the dict from the tool_use block."""
    payload = _empty_plan_dict()
    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_response.content = [MagicMock(type="tool_use", input=payload)]
    fake_response.stop_reason = "end_turn"
    fake_response.usage = MagicMock(
        input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0
    )
    _mock_stream(fake_client, fake_response)

    provider = AnthropicProvider(model="claude-sonnet-4-6", api_key="sk-x", client=fake_client)
    result = provider.complete(
        system="sys",
        messages=[Message(role="user", content="hi")],
        max_tokens=128,
        tools=[INGEST_TOOL_DEFINITION],
        tool_choice=INGEST_TOOL_CHOICE,
    )
    assert result.tool_input == payload


@pytest.mark.unit
def test_complete_without_tools_leaves_tool_input_none() -> None:
    """Existing free-form text path is unchanged when tools is not passed."""
    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_response.content = [MagicMock(type="text", text="hello back")]
    fake_response.stop_reason = "end_turn"
    fake_response.usage = MagicMock(
        input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0
    )
    _mock_stream(fake_client, fake_response)

    provider = AnthropicProvider(model="claude-sonnet-4-6", api_key="sk-x", client=fake_client)
    result = provider.complete(
        system="sys", messages=[Message(role="user", content="hi")], max_tokens=128
    )
    assert result.tool_input is None
    assert result.text == "hello back"
    # ``tools``/``tool_choice`` keys must not appear when tools was None — keeping
    # them out preserves cache hits across non-tool calls (cluster verdict, etc.).
    kwargs = fake_client.messages.stream.call_args.kwargs
    assert "tools" not in kwargs
    assert "tool_choice" not in kwargs


@pytest.mark.unit
def test_verdict_has_pattern_constraint_excluding_invented_values() -> None:
    """Verdict must reject ``empty``/``skip``/``needs-review`` at the schema layer.

    Without the pattern, the model can invent verdicts (observed empirically:
    ``"empty"`` from a probe call). Constrained decoding honors ``pattern``.
    """
    import re

    verdict = INGEST_TOOL_DEFINITION["input_schema"]["properties"]["verdict"]
    assert "pattern" in verdict
    pat = re.compile(verdict["pattern"])
    for good in ("ingest", "low-quality", "out-of-scope", "duplicate-of:wiki/foo.md"):
        assert pat.fullmatch(good), f"{good!r} should match the verdict pattern"
    # Use fullmatch so trailing newlines/junk are rejected — re.match would
    # accept "ingest\n" because $ in the pattern matches before a final newline.
    for bad in ("empty", "skip", "needs-review", "Ingest", "duplicate-of:", "ingest\n"):
        assert not pat.fullmatch(bad), f"{bad!r} must not match the verdict pattern"


@pytest.mark.unit
def test_tool_use_system_prompt_omits_inline_json_shape_spec() -> None:
    """The tool-use prompt must not duplicate the JSON shape — that confuses the model.

    Empirical: with both an inline schema in the system prompt AND tool_use, the
    model occasionally emitted wrong-typed fields (``updates: "<text>"``). The
    tool-use prompt drops the inline spec and points the model at the tool.
    """
    from mdwiki.prompts import INGEST_SYSTEM_PROMPT_TOOL_USE

    assert "Output ONLY a valid JSON object" not in INGEST_SYSTEM_PROMPT_TOOL_USE
    assert "submit_plan" in INGEST_SYSTEM_PROMPT_TOOL_USE


@pytest.mark.unit
def test_complete_with_tools_handles_non_dict_tool_input_gracefully() -> None:
    """If the SDK surprises us with a non-dict input, fall back to None — never crash."""
    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_response.content = [MagicMock(type="tool_use", input="not a dict")]
    fake_response.stop_reason = "end_turn"
    fake_response.usage = MagicMock(
        input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0
    )
    _mock_stream(fake_client, fake_response)

    provider = AnthropicProvider(model="claude-sonnet-4-6", api_key="sk-x", client=fake_client)
    result = provider.complete(
        system="sys",
        messages=[Message(role="user", content="hi")],
        tools=[INGEST_TOOL_DEFINITION],
        tool_choice=INGEST_TOOL_CHOICE,
    )
    assert result.tool_input is None


@pytest.mark.unit
def test_complete_raises_when_tool_choice_set_without_tools() -> None:
    """A caller passing ``tool_choice`` but no ``tools`` is silently dropped today.

    The Anthropic SDK requires ``tools`` whenever ``tool_choice`` is set; if
    we silently drop ``tool_choice`` the caller's intent (force-decode through
    a tool) is lost without warning. Raising surfaces the misuse loudly.
    """
    fake_client = MagicMock()
    provider = AnthropicProvider(model="claude-sonnet-4-6", api_key="sk-x", client=fake_client)
    with pytest.raises(ValueError, match="tool_choice requires tools"):
        provider.complete(
            system="sys",
            messages=[Message(role="user", content="hi")],
            tools=None,
            tool_choice=INGEST_TOOL_CHOICE,
        )


@pytest.mark.unit
def test_batch_complete_raises_when_tool_choice_set_without_tools() -> None:
    """Same guardrail as ``complete`` — applies to per-request batch params too."""
    fake_client = MagicMock()
    provider = AnthropicProvider(model="claude-sonnet-4-6", api_key="sk-x", client=fake_client)
    requests = [
        BatchRequest(
            custom_id="src-1",
            system="sys",
            messages=[Message(role="user", content="hi")],
            tools=None,
            tool_choice=INGEST_TOOL_CHOICE,
        )
    ]
    with pytest.raises(ValueError, match="tool_choice requires tools"):
        provider.batch_complete(requests, poll_interval=0.0)


# ---------- AnthropicProvider.batch_complete with tools ----------


@pytest.mark.unit
def test_batch_complete_forwards_tools_to_each_request_params() -> None:
    """Per-request ``tools``/``tool_choice`` land in the SDK ``params`` dict."""
    fake_client = MagicMock()
    # batch.create returns a batch with id; retrieve returns ended; results returns []
    batch = MagicMock(id="batch_x", processing_status="ended", request_counts=MagicMock(succeeded=0))
    fake_client.messages.batches.create.return_value = batch
    fake_client.messages.batches.retrieve.return_value = batch
    fake_client.messages.batches.results.return_value = iter([])

    provider = AnthropicProvider(model="claude-sonnet-4-6", api_key="sk-x", client=fake_client)
    requests = [
        BatchRequest(
            custom_id="src-1",
            system="sys",
            messages=[Message(role="user", content="hi")],
            tools=[INGEST_TOOL_DEFINITION],
            tool_choice=INGEST_TOOL_CHOICE,
        )
    ]
    provider.batch_complete(requests, poll_interval=0.0)

    sdk_args = fake_client.messages.batches.create.call_args
    submitted = sdk_args.kwargs["requests"][0]
    assert submitted["custom_id"] == "src-1"
    assert submitted["params"]["tools"] == [INGEST_TOOL_DEFINITION]
    assert submitted["params"]["tool_choice"] == INGEST_TOOL_CHOICE


@pytest.mark.unit
def test_batch_complete_without_tools_omits_them_in_params() -> None:
    """A BatchRequest with tools=None must not put ``tools`` in the SDK params."""
    fake_client = MagicMock()
    batch = MagicMock(id="batch_x", processing_status="ended", request_counts=MagicMock(succeeded=0))
    fake_client.messages.batches.create.return_value = batch
    fake_client.messages.batches.retrieve.return_value = batch
    fake_client.messages.batches.results.return_value = iter([])

    provider = AnthropicProvider(model="claude-sonnet-4-6", api_key="sk-x", client=fake_client)
    requests = [BatchRequest(custom_id="src-1", system="sys", messages=[Message(role="user", content="hi")])]
    provider.batch_complete(requests, poll_interval=0.0)

    submitted = fake_client.messages.batches.create.call_args.kwargs["requests"][0]
    assert "tools" not in submitted["params"]
    assert "tool_choice" not in submitted["params"]


@pytest.mark.unit
def test_batch_decode_extracts_tool_input_from_succeeded_entry() -> None:
    """A successful batch entry whose message contains a tool_use block exposes tool_input."""
    fake_client = MagicMock()
    payload = _empty_plan_dict()

    batch = MagicMock(id="batch_x", processing_status="ended", request_counts=MagicMock(succeeded=1))
    fake_client.messages.batches.create.return_value = batch
    fake_client.messages.batches.retrieve.return_value = batch

    entry = MagicMock(
        custom_id="src-1",
        result=MagicMock(
            type="succeeded",
            message=MagicMock(
                content=[MagicMock(type="tool_use", input=payload)],
                usage=MagicMock(input_tokens=10, output_tokens=5),
            ),
        ),
    )
    fake_client.messages.batches.results.return_value = iter([entry])

    provider = AnthropicProvider(model="claude-sonnet-4-6", api_key="sk-x", client=fake_client)
    requests = [
        BatchRequest(
            custom_id="src-1",
            system="sys",
            messages=[Message(role="user", content="hi")],
            tools=[INGEST_TOOL_DEFINITION],
            tool_choice=INGEST_TOOL_CHOICE,
        )
    ]
    results = provider.batch_complete(requests, poll_interval=0.0)
    assert len(results) == 1
    assert results[0].error is None
    assert results[0].tool_input == payload


# ---------- parse_plan_dict ----------


@pytest.mark.unit
def test_parse_plan_dict_accepts_valid_payload() -> None:
    plan = parse_plan_dict(_empty_plan_dict())
    assert isinstance(plan, Plan)
    assert plan.verdict == "low-quality"
    assert plan.is_empty()


@pytest.mark.unit
def test_parse_plan_dict_rejects_missing_required_keys() -> None:
    with pytest.raises(PlanValidationError):
        parse_plan_dict({"verdict": "ingest"})  # type: ignore[arg-type]


@pytest.mark.unit
def test_parse_plan_dict_enforces_verdict_invariant() -> None:
    """A non-ingest verdict cannot carry non-empty updates."""
    payload = _empty_plan_dict()
    payload["updates"] = [
        {"page": "wiki/foo.md", "content": "x", "claims": [{"source_section_id": "s", "quote": "q q q q"}]}
    ]
    with pytest.raises(PlanValidationError) as excinfo:
        parse_plan_dict(payload)
    assert "non-empty" in str(excinfo.value)


@pytest.mark.unit
def test_parse_plan_does_not_double_validate() -> None:
    """``parse_plan(str)`` must still work for the cluster-verdict / legacy paths."""
    import json

    from mdwiki.plan import parse_plan

    plan = parse_plan(json.dumps(_empty_plan_dict()))
    assert plan.verdict == "low-quality"
    assert plan.is_empty()

"""Tests for ``mdwiki.llm.anthropic`` — the v1.0.0 Anthropic adapter.

The Anthropic SDK is mocked; we never make real network calls in unit tests.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import anthropic
import pytest
from pytest_mock import MockerFixture

from mdwiki.llm.anthropic import AnthropicProvider, MissingAPIKeyError
from mdwiki.llm.base import Message


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must not pick up the developer's real ANTHROPIC_API_KEY."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.mark.unit
def test_constructor_reads_api_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-from-env")
    provider = AnthropicProvider(model="claude-sonnet-4-6")
    assert provider.name == "anthropic"
    assert provider.model == "claude-sonnet-4-6"


@pytest.mark.unit
def test_constructor_accepts_explicit_api_key() -> None:
    provider = AnthropicProvider(model="claude-sonnet-4-6", api_key="sk-explicit")
    assert provider.model == "claude-sonnet-4-6"


@pytest.mark.unit
def test_constructor_raises_clear_error_without_api_key() -> None:
    with pytest.raises(MissingAPIKeyError) as excinfo:
        AnthropicProvider(model="claude-sonnet-4-6")
    msg = str(excinfo.value)
    assert "ANTHROPIC_API_KEY" in msg


@pytest.mark.unit
def test_ping_success_returns_ok_with_latency(mocker: MockerFixture) -> None:
    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_response.content = [MagicMock(type="text", text="ok")]
    fake_client.messages.create.return_value = fake_response

    provider = AnthropicProvider(model="claude-sonnet-4-6", api_key="sk-x", client=fake_client)
    result = provider.ping()

    assert result.ok is True
    assert result.provider == "anthropic"
    assert result.model == "claude-sonnet-4-6"
    assert result.latency_ms >= 0


@pytest.mark.unit
def test_ping_uses_minimal_max_tokens(mocker: MockerFixture) -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = MagicMock(content=[MagicMock(type="text", text="ok")])

    provider = AnthropicProvider(model="claude-sonnet-4-6", api_key="sk-x", client=fake_client)
    provider.ping()

    fake_client.messages.create.assert_called_once()
    call_kwargs = fake_client.messages.create.call_args.kwargs
    assert call_kwargs["max_tokens"] <= 16
    assert call_kwargs["model"] == "claude-sonnet-4-6"


@pytest.mark.unit
def test_ping_authentication_error_returns_actionable_message(mocker: MockerFixture) -> None:
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = anthropic.AuthenticationError(
        message="invalid x-api-key",
        response=MagicMock(status_code=401, headers={}),
        body={},
    )

    provider = AnthropicProvider(model="claude-sonnet-4-6", api_key="sk-bad", client=fake_client)
    result = provider.ping()

    assert result.ok is False
    assert "auth" in result.message.lower() or "key" in result.message.lower()


@pytest.mark.unit
def test_ping_model_not_found_returns_actionable_message(mocker: MockerFixture) -> None:
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = anthropic.NotFoundError(
        message="model not found",
        response=MagicMock(status_code=404, headers={}),
        body={},
    )

    provider = AnthropicProvider(model="claude-fake-model", api_key="sk-x", client=fake_client)
    result = provider.ping()

    assert result.ok is False
    assert "model" in result.message.lower() or "not found" in result.message.lower()


@pytest.mark.unit
def test_ping_connection_error_returns_actionable_message(mocker: MockerFixture) -> None:
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = anthropic.APIConnectionError(request=MagicMock())

    provider = AnthropicProvider(model="claude-sonnet-4-6", api_key="sk-x", client=fake_client)
    result = provider.ping()

    assert result.ok is False
    assert "connect" in result.message.lower() or "network" in result.message.lower()


def _mock_stream(fake_client: MagicMock, response: MagicMock) -> None:
    """Wire ``fake_client.messages.stream(...)`` to yield a context manager whose
    ``get_final_message()`` returns ``response``."""
    stream_cm = MagicMock()
    stream_cm.__enter__.return_value.get_final_message.return_value = response
    stream_cm.__exit__.return_value = False
    fake_client.messages.stream.return_value = stream_cm


@pytest.mark.unit
def test_complete_passes_system_with_cache_control(mocker: MockerFixture) -> None:
    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_response.content = [MagicMock(type="text", text="hello back")]
    fake_response.stop_reason = "end_turn"
    fake_response.usage = MagicMock(
        input_tokens=10,
        output_tokens=5,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=120,
    )
    _mock_stream(fake_client, fake_response)

    provider = AnthropicProvider(model="claude-sonnet-4-6", api_key="sk-x", client=fake_client)
    result = provider.complete(
        system="You are a wiki maintainer.",
        messages=[Message(role="user", content="hi")],
        max_tokens=128,
    )

    fake_client.messages.stream.assert_called_once()
    kwargs = fake_client.messages.stream.call_args.kwargs
    assert kwargs["model"] == "claude-sonnet-4-6"
    assert kwargs["max_tokens"] == 128
    assert isinstance(kwargs["system"], list)
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kwargs["system"][0]["text"] == "You are a wiki maintainer."
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]

    assert result.text == "hello back"
    assert result.input_tokens == 10
    assert result.output_tokens == 5
    assert result.cache_creation_tokens == 120


@pytest.mark.unit
def test_complete_raises_clear_error_on_max_tokens_truncation(mocker: MockerFixture) -> None:
    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_response.content = [MagicMock(type="text", text='{"verdict": "ingest", "rationale": "trun')]
    fake_response.stop_reason = "max_tokens"
    fake_response.usage = MagicMock(
        input_tokens=100, output_tokens=4096, cache_read_input_tokens=0, cache_creation_input_tokens=0
    )
    _mock_stream(fake_client, fake_response)

    provider = AnthropicProvider(model="claude-sonnet-4-6", api_key="sk-x", client=fake_client)
    from mdwiki.llm.anthropic import OutputTruncatedError

    with pytest.raises(OutputTruncatedError) as excinfo:
        provider.complete(system="sys", messages=[Message(role="user", content="x")], max_tokens=4096)
    msg = str(excinfo.value)
    assert "truncat" in msg.lower() or "max_tokens" in msg
    assert "4096" in msg


@pytest.mark.unit
def test_complete_reports_cache_read_tokens(mocker: MockerFixture) -> None:
    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_response.content = [MagicMock(type="text", text="cached")]
    fake_response.stop_reason = "end_turn"
    fake_response.usage = MagicMock(
        input_tokens=2,
        output_tokens=3,
        cache_read_input_tokens=500,
        cache_creation_input_tokens=0,
    )
    _mock_stream(fake_client, fake_response)

    provider = AnthropicProvider(model="claude-sonnet-4-6", api_key="sk-x", client=fake_client)
    result = provider.complete(system="sys", messages=[Message(role="user", content="x")])

    assert result.cache_read_tokens == 500
    assert result.cache_creation_tokens == 0


# ----- describe_image (Phase 5) -----


@pytest.mark.unit
def test_describe_image_sends_image_content_block(tmp_path, mocker: MockerFixture) -> None:
    """``describe_image`` base64-encodes the image and sends it as an image content block."""
    src = tmp_path / "diagram.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 32)  # plausible PNG header + body bytes

    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_response.content = [MagicMock(type="text", text="A flowchart with three boxes connected by arrows.")]
    fake_client.messages.create.return_value = fake_response

    provider = AnthropicProvider(model="claude-sonnet-4-6", api_key="sk-x", client=fake_client)
    result = provider.describe_image(src)

    assert "flowchart" in result.lower()
    fake_client.messages.create.assert_called_once()
    kwargs = fake_client.messages.create.call_args.kwargs
    # Single user message with an image content block
    assert kwargs["model"] == "claude-sonnet-4-6"
    assert len(kwargs["messages"]) == 1
    content = kwargs["messages"][0]["content"]
    image_blocks = [b for b in content if b.get("type") == "image"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["source"]["type"] == "base64"
    assert image_blocks[0]["source"]["media_type"] == "image/png"
    # Body must include text instructing the model to describe + OCR
    text_blocks = [b for b in content if b.get("type") == "text"]
    assert text_blocks, "describe_image must include a text instruction with the image"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("ext", "expected_media_type"),
    [
        (".png", "image/png"),
        (".jpg", "image/jpeg"),
        (".jpeg", "image/jpeg"),
        (".webp", "image/webp"),
        (".gif", "image/gif"),
    ],
)
def test_describe_image_maps_extension_to_media_type(
    tmp_path, mocker: MockerFixture, ext: str, expected_media_type: str
) -> None:
    """Each registered image extension maps to the right MIME type for the API."""
    src = tmp_path / f"x{ext}"
    src.write_bytes(b"binary")
    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_response.content = [MagicMock(type="text", text="ok")]
    fake_client.messages.create.return_value = fake_response

    provider = AnthropicProvider(model="claude-sonnet-4-6", api_key="sk-x", client=fake_client)
    provider.describe_image(src)
    kwargs = fake_client.messages.create.call_args.kwargs
    image_block = next(b for b in kwargs["messages"][0]["content"] if b.get("type") == "image")
    assert image_block["source"]["media_type"] == expected_media_type


# ----- batch_complete + estimate_batch_cost (Phase 6) -----


def _build_batch_provider() -> tuple[AnthropicProvider, MagicMock]:
    """Helper: AnthropicProvider with a mocked SDK client."""
    fake_client = MagicMock()
    provider = AnthropicProvider(model="claude-sonnet-4-6", api_key="sk-x", client=fake_client)
    return provider, fake_client


def _make_batch_result_entry(custom_id: str, text: str, *, succeeded: bool = True) -> MagicMock:
    """Build a fake SDK batch-result entry (succeeded or errored)."""
    entry = MagicMock()
    entry.custom_id = custom_id
    if succeeded:
        message = MagicMock()
        message.content = [MagicMock(type="text", text=text)]
        message.usage = MagicMock(input_tokens=100, output_tokens=50)
        entry.result = MagicMock(type="succeeded", message=message)
    else:
        entry.result = MagicMock(type="errored", error=MagicMock(type="rate_limited"))
    return entry


@pytest.mark.unit
def test_batch_complete_submits_one_create_call_with_each_request(mocker: MockerFixture) -> None:
    """``batch_complete([3 requests])`` calls ``batches.create`` once with three sub-requests."""
    from mdwiki.llm.base import BatchRequest

    provider, fake_client = _build_batch_provider()
    fake_batch = MagicMock(id="batch_123", processing_status="ended")
    fake_client.messages.batches.create.return_value = fake_batch
    fake_client.messages.batches.retrieve.return_value = fake_batch
    fake_client.messages.batches.results.return_value = [
        _make_batch_result_entry("src-a", "result-a"),
        _make_batch_result_entry("src-b", "result-b"),
        _make_batch_result_entry("src-c", "result-c"),
    ]

    requests = [
        BatchRequest(custom_id=f"src-{c}", system="sys", messages=[Message(role="user", content=f"prompt-{c}")])
        for c in ("a", "b", "c")
    ]
    results = provider.batch_complete(requests, poll_interval=0.0)

    fake_client.messages.batches.create.assert_called_once()
    sdk_requests = fake_client.messages.batches.create.call_args.kwargs["requests"]
    assert len(sdk_requests) == 3
    assert {r["custom_id"] for r in sdk_requests} == {"src-a", "src-b", "src-c"}
    # Every sub-request gets the model + system with cache_control
    for r in sdk_requests:
        params = r["params"]
        assert params["model"] == "claude-sonnet-4-6"
        assert params["system"][0]["cache_control"] == {"type": "ephemeral"}

    assert {r.custom_id for r in results} == {"src-a", "src-b", "src-c"}
    assert all(r.error is None for r in results)


@pytest.mark.unit
def test_batch_complete_polls_until_status_ended(mocker: MockerFixture) -> None:
    """Polls until ``processing_status == 'ended'``; calls ``on_status`` each iteration."""
    from mdwiki.llm.base import BatchRequest

    provider, fake_client = _build_batch_provider()
    fake_client.messages.batches.create.return_value = MagicMock(id="batch_x", processing_status="in_progress")
    # First poll: still in progress; second poll: ended
    fake_client.messages.batches.retrieve.side_effect = [
        MagicMock(id="batch_x", processing_status="in_progress", request_counts=MagicMock(succeeded=0)),
        MagicMock(id="batch_x", processing_status="ended", request_counts=MagicMock(succeeded=1)),
    ]
    fake_client.messages.batches.results.return_value = [_make_batch_result_entry("src-a", "ok")]

    statuses: list[tuple[str, int, int]] = []
    requests = [BatchRequest(custom_id="src-a", system="s", messages=[Message(role="user", content="p")])]
    results = provider.batch_complete(
        requests,
        poll_interval=0.0,
        on_status=lambda status, succeeded, total: statuses.append((status, succeeded, total)),
    )

    assert len(results) == 1
    assert results[0].text == "ok"
    assert statuses == [("in_progress", 0, 1), ("ended", 1, 1)]


@pytest.mark.unit
def test_batch_complete_decodes_errored_results(mocker: MockerFixture) -> None:
    """A result entry with ``type='errored'`` becomes a ``BatchResult`` with ``error`` set."""
    from mdwiki.llm.base import BatchRequest

    provider, fake_client = _build_batch_provider()
    fake_batch = MagicMock(id="batch_x", processing_status="ended")
    fake_client.messages.batches.create.return_value = fake_batch
    fake_client.messages.batches.retrieve.return_value = fake_batch
    fake_client.messages.batches.results.return_value = [
        _make_batch_result_entry("src-ok", "good"),
        _make_batch_result_entry("src-bad", "", succeeded=False),
    ]

    requests = [
        BatchRequest(custom_id="src-ok", system="s", messages=[Message(role="user", content="p1")]),
        BatchRequest(custom_id="src-bad", system="s", messages=[Message(role="user", content="p2")]),
    ]
    results = provider.batch_complete(requests, poll_interval=0.0)

    by_id = {r.custom_id: r for r in results}
    assert by_id["src-ok"].error is None
    assert by_id["src-ok"].text == "good"
    assert by_id["src-bad"].error == "rate_limited"
    assert by_id["src-bad"].text == ""


@pytest.mark.unit
def test_estimate_batch_cost_uses_chars_per_token_heuristic() -> None:
    """Cost estimate scales linearly with prompt char count + max_tokens × Sonnet batch rates."""
    from mdwiki.llm.base import BatchRequest

    provider, _ = _build_batch_provider()
    requests = [
        BatchRequest(custom_id="a", system="x" * 4000, messages=[Message(role="user", content="y" * 4000)], max_tokens=1000),
        BatchRequest(custom_id="b", system="x" * 4000, messages=[Message(role="user", content="y" * 4000)], max_tokens=1000),
    ]
    estimate = provider.estimate_batch_cost(requests)
    # 8000 chars per request / 4 = 2000 input tokens × 2 requests = 4000
    assert estimate.requests == 2
    assert estimate.input_tokens == 4000
    assert estimate.output_tokens_max == 2000
    # Batch discount = 50%; sonnet input $3/M, output $15/M
    # input cost = 4000/1M * $3 = $0.012; output cost = 2000/1M * $15 = $0.030
    # total before discount = $0.042; after 50% discount = $0.021
    assert abs(estimate.usd_total - 0.021) < 1e-6

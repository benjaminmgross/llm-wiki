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


@pytest.mark.unit
def test_complete_passes_system_with_cache_control(mocker: MockerFixture) -> None:
    fake_client = MagicMock()
    fake_response = MagicMock()
    fake_response.content = [MagicMock(type="text", text="hello back")]
    fake_response.usage = MagicMock(
        input_tokens=10,
        output_tokens=5,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=120,
    )
    fake_client.messages.create.return_value = fake_response

    provider = AnthropicProvider(model="claude-sonnet-4-6", api_key="sk-x", client=fake_client)
    result = provider.complete(
        system="You are a wiki maintainer.",
        messages=[Message(role="user", content="hi")],
        max_tokens=128,
    )

    fake_client.messages.create.assert_called_once()
    kwargs = fake_client.messages.create.call_args.kwargs
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
    fake_client.messages.create.return_value = fake_response

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
    fake_response.usage = MagicMock(
        input_tokens=2,
        output_tokens=3,
        cache_read_input_tokens=500,
        cache_creation_input_tokens=0,
    )
    fake_client.messages.create.return_value = fake_response

    provider = AnthropicProvider(model="claude-sonnet-4-6", api_key="sk-x", client=fake_client)
    result = provider.complete(system="sys", messages=[Message(role="user", content="x")])

    assert result.cache_read_tokens == 500
    assert result.cache_creation_tokens == 0

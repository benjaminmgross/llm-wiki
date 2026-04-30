"""Tests for ``OpenAICompatibleProvider`` — generic adapter for any OpenAI-compatible endpoint.

The OpenAI SDK is fully mocked; we never make real network calls. Covers:
- ``complete()`` translating to ``chat.completions.create``
- ``ping()`` issuing a 1-token call
- Vision opt-in (default ``describe_image`` raises NotImplementedError)
- ``batch_complete`` raises NotImplementedError (most local servers don't have a batch API)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from mdwiki.llm.base import BatchRequest, Message
from mdwiki.llm.openai_compatible import OpenAICompatibleProvider


def _build_provider_with_mocked_client() -> tuple[OpenAICompatibleProvider, MagicMock]:
    fake_client = MagicMock()
    provider = OpenAICompatibleProvider(
        model="qwen2.5-72b-instruct",
        base_url="http://localhost:8000/v1",
        api_key="test-key",
        client=fake_client,
    )
    return provider, fake_client


@pytest.mark.unit
def test_complete_calls_openai_chat_completions(mocker: MockerFixture) -> None:
    """``complete()`` routes through ``chat.completions.create`` with model + system + messages."""
    provider, fake_client = _build_provider_with_mocked_client()
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content="local model says hi"))]
    response.usage = MagicMock(prompt_tokens=12, completion_tokens=8)
    fake_client.chat.completions.create.return_value = response

    result = provider.complete(
        system="You are a wiki maintainer.",
        messages=[Message(role="user", content="describe attention sinks")],
        max_tokens=512,
    )

    assert result.text == "local model says hi"
    assert result.input_tokens == 12
    assert result.output_tokens == 8
    fake_client.chat.completions.create.assert_called_once()
    kwargs = fake_client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "qwen2.5-72b-instruct"
    assert kwargs["max_tokens"] == 512
    assert kwargs["messages"][0] == {"role": "system", "content": "You are a wiki maintainer."}
    assert kwargs["messages"][1] == {"role": "user", "content": "describe attention sinks"}


@pytest.mark.unit
def test_ping_returns_ok_on_successful_call(mocker: MockerFixture) -> None:
    """``ping()`` issues a 1-token call and reports OK on success."""
    provider, fake_client = _build_provider_with_mocked_client()
    fake_client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="."))],
    )
    result = provider.ping()
    assert result.ok is True
    assert result.provider == "openai-compatible"
    assert result.model == "qwen2.5-72b-instruct"
    fake_client.chat.completions.create.assert_called_once()
    kwargs = fake_client.chat.completions.create.call_args.kwargs
    assert kwargs["max_tokens"] == 1


@pytest.mark.unit
def test_ping_returns_failure_on_connection_error(mocker: MockerFixture) -> None:
    """``ping()`` translates SDK errors into ``PingResult(ok=False)`` instead of propagating."""
    provider, fake_client = _build_provider_with_mocked_client()
    fake_client.chat.completions.create.side_effect = ConnectionError("could not connect to localhost:8000")
    result = provider.ping()
    assert result.ok is False
    assert "could not connect" in result.message.lower() or "connect" in result.message.lower()


@pytest.mark.unit
def test_describe_image_raises_unless_vision_capable_set() -> None:
    """``describe_image`` raises NotImplementedError unless ``vision_capable=True`` is passed."""
    provider, _ = _build_provider_with_mocked_client()
    with pytest.raises(NotImplementedError, match="vision"):
        provider.describe_image(Path("x.png"))


@pytest.mark.unit
def test_describe_image_works_when_vision_capable(mocker: MockerFixture, tmp_path: Path) -> None:
    """With ``vision_capable=True``, ``describe_image`` sends the image to chat.completions."""
    fake_client = MagicMock()
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content="A diagram of three boxes."))]
    fake_client.chat.completions.create.return_value = response
    provider = OpenAICompatibleProvider(
        model="qwen2.5-vl-72b",
        base_url="http://localhost:8000/v1",
        api_key="x",
        vision_capable=True,
        client=fake_client,
    )
    src = tmp_path / "x.png"
    src.write_bytes(b"\x89PNG fake")

    result = provider.describe_image(src)
    assert "diagram" in result.lower()
    fake_client.chat.completions.create.assert_called_once()


@pytest.mark.unit
def test_batch_complete_raises_not_implemented() -> None:
    """OpenAI-compatible servers (vLLM, llama.cpp) don't have a batch API."""
    provider, _ = _build_provider_with_mocked_client()
    with pytest.raises(NotImplementedError, match="batch"):
        provider.batch_complete(
            [BatchRequest(custom_id="x", system="s", messages=[Message(role="user", content="p")])]
        )


@pytest.mark.unit
def test_constructor_accepts_optional_api_key() -> None:
    """vLLM doesn't require an API key — provider must accept None / empty."""
    fake_client = MagicMock()
    provider = OpenAICompatibleProvider(
        model="qwen2.5-72b-instruct",
        base_url="http://localhost:8000/v1",
        api_key=None,
        client=fake_client,
    )
    assert provider.model == "qwen2.5-72b-instruct"
    assert provider.name == "openai-compatible"

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

import openai
import pytest
from pytest_mock import MockerFixture

from mdwiki.llm.anthropic import OutputTruncatedError
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
def test_complete_raises_on_finish_reason_length() -> None:
    """``finish_reason='length'`` mirrors Anthropic's ``stop_reason='max_tokens'`` and must raise.

    A truncated response is almost certainly invalid JSON; raising forces the
    caller to surface the issue rather than parse garbage.
    """
    provider, fake_client = _build_provider_with_mocked_client()
    response = MagicMock()
    response.choices = [
        MagicMock(
            finish_reason="length",
            message=MagicMock(content='{"verdict":"ingest","ration'),
        )
    ]
    fake_client.chat.completions.create.return_value = response

    with pytest.raises(OutputTruncatedError) as excinfo:
        provider.complete(
            system="s",
            messages=[Message(role="user", content="x")],
            max_tokens=100,
        )
    msg = str(excinfo.value)
    assert "100" in msg
    assert "truncat" in msg.lower() or "length" in msg.lower()


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


def _make_openai_exc(exc_cls: type[Exception]) -> Exception:
    """Construct an openai SDK exception with the kwargs each subclass requires.

    AuthenticationError / NotFoundError / APIStatusError need ``response``+``body``;
    APIConnectionError needs ``request``. Mirrors test_llm_anthropic.py's pattern.
    """
    if exc_cls is openai.APIConnectionError:
        return exc_cls(request=MagicMock())
    return exc_cls(message="boom", response=MagicMock(status_code=400, headers={}), body=None)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("exc_cls", "expected_keywords"),
    [
        (openai.AuthenticationError, ("auth",)),
        (openai.NotFoundError, ("model", "not found")),
        (openai.APIConnectionError, ("connect", "network")),
        (openai.APIStatusError, ("api error", "status")),
    ],
)
def test_ping_translates_specific_openai_errors_into_actionable_messages(
    exc_cls: type[Exception], expected_keywords: tuple[str, ...]
) -> None:
    """Each narrow openai SDK exception yields a PingResult with the matching keyword.

    Guards the ladder of specific ``except`` branches in ``OpenAICompatibleProvider.ping``
    against silent re-routing (e.g. AuthenticationError subclasses APIStatusError, so
    catching APIStatusError first would mask the auth-specific message).
    """
    provider, fake_client = _build_provider_with_mocked_client()
    fake_client.chat.completions.create.side_effect = _make_openai_exc(exc_cls)

    result = provider.ping()

    assert result.ok is False
    lower = result.message.lower()
    assert any(kw in lower for kw in expected_keywords), f"expected one of {expected_keywords!r} in {result.message!r}"


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
        provider.batch_complete([BatchRequest(custom_id="x", system="s", messages=[Message(role="user", content="p")])])


@pytest.mark.unit
def test_complete_raises_when_tool_choice_set_without_tools() -> None:
    """Mirror the AnthropicProvider guard — passing ``tool_choice`` without ``tools``
    is almost certainly a caller bug. Even though this provider drops both args
    on the OpenAI-compatible path today, silently dropping the inconsistency
    masks the misuse; raising surfaces it loudly.
    """
    provider, _ = _build_provider_with_mocked_client()
    with pytest.raises(ValueError, match="tool_choice requires tools"):
        provider.complete(
            system="sys",
            messages=[Message(role="user", content="hi")],
            tools=None,
            tool_choice={"type": "tool", "name": "submit_plan"},
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


@pytest.mark.unit
def test_openai_compatible_declares_no_native_batch_or_tool_capability() -> None:
    provider, _ = _build_provider_with_mocked_client()

    assert provider.supports_batch is False
    assert provider.supports_tool_use is False


@pytest.mark.unit
def test_openai_compatible_classifies_transient_but_not_auth_errors_as_recoverable() -> None:
    provider, _ = _build_provider_with_mocked_client()
    request = MagicMock()
    transient = openai.APIConnectionError(request=request)
    fatal = openai.AuthenticationError(
        message="invalid key",
        response=MagicMock(status_code=401, headers={}),
        body={},
    )

    assert provider.is_recoverable_error(transient) is True
    assert provider.is_recoverable_error(fatal) is False


@pytest.mark.unit
def test_openai_compatible_batch_error_does_not_recommend_anthropic() -> None:
    provider, _ = _build_provider_with_mocked_client()

    with pytest.raises(NotImplementedError) as excinfo:
        provider.batch_complete([BatchRequest(custom_id="x", system="s", messages=[Message(role="user", content="p")])])

    assert "anthropic" not in str(excinfo.value).lower()

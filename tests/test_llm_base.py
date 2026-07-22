"""Tests for the ``mdwiki.llm.base`` Provider ABC."""

from __future__ import annotations

import pytest

from mdwiki.llm.base import CompleteResult, Message, PingResult, Provider


@pytest.mark.unit
def test_provider_is_abstract() -> None:
    with pytest.raises(TypeError):
        Provider()  # type: ignore[abstract]


@pytest.mark.unit
def test_subclass_without_methods_cannot_instantiate() -> None:
    class Incomplete(Provider):
        name = "incomplete"

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]


@pytest.mark.unit
def test_subclass_with_methods_instantiates() -> None:
    class Stub(Provider):
        name = "stub"

        def __init__(self) -> None:
            self.model = "stub-model"

        def ping(self) -> PingResult:
            return PingResult(provider="stub", model="stub-model", latency_ms=1.0, ok=True, message="pong")

        def complete(self, *, system: str, messages: list[Message], max_tokens: int = 1024) -> CompleteResult:
            return CompleteResult(text="hi", input_tokens=1, output_tokens=1)

    stub = Stub()
    assert stub.ping().ok is True


@pytest.mark.unit
def test_provider_capabilities_default_to_safe_opt_outs() -> None:
    class Stub(Provider):
        name = "stub"

        def ping(self) -> PingResult:
            return PingResult(provider=self.name, model="stub", latency_ms=0.0, ok=True, message="pong")

        def complete(self, *, system: str, messages: list[Message], max_tokens: int = 1024) -> CompleteResult:
            return CompleteResult(text="ok", input_tokens=1, output_tokens=1)

    provider = Stub()

    assert provider.supports_batch is False
    assert provider.supports_tool_use is False
    assert provider.is_recoverable_error(RuntimeError("boom")) is False


@pytest.mark.unit
def test_message_is_frozen() -> None:
    msg = Message(role="user", content="hi")
    with pytest.raises(Exception):
        msg.role = "assistant"  # type: ignore[misc]


@pytest.mark.unit
def test_complete_result_defaults_zero_cache_counters() -> None:
    result = CompleteResult(text="ok", input_tokens=10, output_tokens=5)
    assert result.cache_read_tokens == 0
    assert result.cache_creation_tokens == 0


@pytest.mark.unit
def test_ping_result_carries_required_fields() -> None:
    result = PingResult(provider="anthropic", model="claude-sonnet-4-6", latency_ms=42.0, ok=True, message="pong")
    assert result.provider == "anthropic"
    assert result.ok is True
    assert result.latency_ms == 42.0


@pytest.mark.unit
def test_describe_image_default_raises_not_implemented(tmp_path) -> None:
    """Phase 5: providers can opt out of vision; default raises NotImplementedError.

    This keeps the door open for OpenAI-compatible / local providers to inherit
    the ABC without being forced to implement describe_image — they raise
    NotImplementedError until the user configures a vision-capable model.
    """

    class TextOnlyProvider(Provider):
        name = "text-only"

        def __init__(self) -> None:
            self.model = "stub"

        def ping(self) -> PingResult:
            return PingResult(provider="text-only", model="stub", latency_ms=1.0, ok=True, message="pong")

        def complete(self, *, system, messages, max_tokens=1024):  # type: ignore[no-untyped-def]
            return CompleteResult(text="ok", input_tokens=1, output_tokens=1)

    provider = TextOnlyProvider()
    with pytest.raises(NotImplementedError, match="describe_image"):
        provider.describe_image(tmp_path / "x.png")

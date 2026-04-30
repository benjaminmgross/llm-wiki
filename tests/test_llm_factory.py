"""Tests for the ``mdwiki.llm`` factory — turn ``config.toml [llm]`` into a Provider."""

from __future__ import annotations

from pathlib import Path

import pytest

from mdwiki.init import init_wiki
from mdwiki.llm import UnknownProviderError, build_provider_from_config
from mdwiki.llm.anthropic import AnthropicProvider


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")


@pytest.mark.unit
def test_factory_returns_anthropic_provider_from_default_config(tmp_path: Path) -> None:
    init_wiki(tmp_path)
    provider = build_provider_from_config(tmp_path)
    assert isinstance(provider, AnthropicProvider)
    assert provider.model == "claude-sonnet-4-6"


@pytest.mark.unit
def test_factory_raises_for_unknown_provider(tmp_path: Path) -> None:
    init_wiki(tmp_path)
    config_path = tmp_path / ".mdwiki" / "config.toml"
    config_path.write_text('[llm]\nprovider = "qwen"\nmodel = "qwen-72b"\n')

    with pytest.raises(UnknownProviderError) as excinfo:
        build_provider_from_config(tmp_path)
    assert "qwen" in str(excinfo.value)
    assert "anthropic" in str(excinfo.value).lower()


@pytest.mark.unit
def test_factory_passes_custom_model_through(tmp_path: Path) -> None:
    init_wiki(tmp_path)
    config_path = tmp_path / ".mdwiki" / "config.toml"
    config_path.write_text('[llm]\nprovider = "anthropic"\nmodel = "claude-haiku-4-5"\n')

    provider = build_provider_from_config(tmp_path)
    assert provider.model == "claude-haiku-4-5"


@pytest.mark.unit
def test_factory_returns_openai_compatible_when_configured(tmp_path: Path) -> None:
    """``provider = "openai-compatible"`` builds OpenAICompatibleProvider with config-driven base_url."""
    from mdwiki.llm.openai_compatible import OpenAICompatibleProvider

    init_wiki(tmp_path)
    config_path = tmp_path / ".mdwiki" / "config.toml"
    config_path.write_text(
        '[llm]\nprovider = "openai-compatible"\nmodel = "qwen2.5-72b-instruct"\n'
        '[llm.openai_compatible]\nbase_url = "http://localhost:8000/v1"\napi_key = "vllm"\n'
    )
    provider = build_provider_from_config(tmp_path)
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.model == "qwen2.5-72b-instruct"
    assert provider.base_url == "http://localhost:8000/v1"


@pytest.mark.unit
def test_factory_openai_compatible_works_without_api_key(tmp_path: Path) -> None:
    """vLLM doesn't require an API key — config without [llm.openai_compatible].api_key still builds."""
    from mdwiki.llm.openai_compatible import OpenAICompatibleProvider

    init_wiki(tmp_path)
    config_path = tmp_path / ".mdwiki" / "config.toml"
    config_path.write_text(
        '[llm]\nprovider = "openai-compatible"\nmodel = "qwen2.5-72b"\n'
        '[llm.openai_compatible]\nbase_url = "http://localhost:8000/v1"\n'
    )
    provider = build_provider_from_config(tmp_path)
    assert isinstance(provider, OpenAICompatibleProvider)


@pytest.mark.unit
def test_factory_openai_compatible_raises_when_base_url_missing(tmp_path: Path) -> None:
    """Without a base_url the openai-compatible provider can't know where to point."""
    init_wiki(tmp_path)
    config_path = tmp_path / ".mdwiki" / "config.toml"
    config_path.write_text(
        '[llm]\nprovider = "openai-compatible"\nmodel = "qwen2.5-72b"\n'
    )
    with pytest.raises(ValueError, match="base_url"):
        build_provider_from_config(tmp_path)

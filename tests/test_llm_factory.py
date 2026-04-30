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

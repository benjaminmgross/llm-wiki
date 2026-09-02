"""Tests for the user-level configuration layer (``$XDG_CONFIG_HOME/mdwiki/config.toml``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mdwiki.config import load_config, user_config_path
from mdwiki.init import init_wiki


@pytest.fixture
def wiki(tmp_path: Path) -> Path:
    root = tmp_path / "wiki"
    root.mkdir()
    (root / "a.md").write_text("# a")
    init_wiki(root, pointers=())
    return root


@pytest.mark.unit
def test_user_config_path_honors_xdg_then_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert user_config_path() == tmp_path / "xdg" / "mdwiki" / "config.toml"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert user_config_path() == tmp_path / "home" / ".config" / "mdwiki" / "config.toml"


@pytest.mark.unit
def test_load_config_layers_user_under_wiki(wiki: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert load_config(wiki)["llm"]["provider"] == "anthropic"  # no user file yet

    user_file = user_config_path()
    user_file.parent.mkdir(parents=True)
    user_file.write_text(
        '[llm]\nprovider = "openai-compatible"\nmodel = "user-model"\n\n[llm.openai_compatible]\nbase_url = "http://localhost:8000/v1"\napi_key = "user-key"\n'
    )
    merged = load_config(wiki)
    # The wiki's own config.toml carries provider/model, so those win; the user layer supplies the rest.
    assert merged["llm"]["provider"] == "anthropic"
    assert merged["llm"]["model"] == "claude-sonnet-4-6"
    assert merged["llm"]["openai_compatible"]["api_key"] == "user-key"
    assert merged["ingest"]["candidate_top_k"] == 8

    user_file.write_text("this is not toml = [")
    with pytest.raises(ValueError) as excinfo:
        load_config(wiki)
    assert "config.toml" in str(excinfo.value)


@pytest.mark.unit
def test_provider_factory_reads_user_layer(wiki: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mdwiki.llm import build_provider_from_config
    from mdwiki.llm.openai_compatible import OpenAICompatibleProvider

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    config_path = wiki / ".mdwiki" / "config.toml"
    config_path.write_text(config_path.read_text().replace('provider = "anthropic"', 'provider = "openai-compatible"'))
    with pytest.raises(ValueError):
        build_provider_from_config(wiki)  # base_url missing everywhere

    user_file = user_config_path()
    user_file.parent.mkdir(parents=True)
    user_file.write_text('[llm.openai_compatible]\nbase_url = "http://localhost:8000/v1"\napi_key = "user-key"\n')
    provider = build_provider_from_config(wiki)
    assert isinstance(provider, OpenAICompatibleProvider)

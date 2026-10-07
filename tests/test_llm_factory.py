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
def test_factory_openai_compatible_reads_api_key_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hosted OpenAI-compatible providers can keep secrets in environment variables."""
    captured: dict[str, object] = {}

    class FakeOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr("mdwiki.llm.openai_compatible.openai.OpenAI", FakeOpenAI)
    init_wiki(tmp_path)
    config_path = tmp_path / ".mdwiki" / "config.toml"
    config_path.write_text(
        '[llm]\nprovider = "openai-compatible"\nmodel = "minimax/minimax-m3"\n'
        '[llm.openai_compatible]\nbase_url = "https://openrouter.ai/api/v1"\n'
        'api_key_env = "OPENROUTER_API_KEY"\n'
    )

    build_provider_from_config(tmp_path)

    assert captured["api_key"] == "sk-or-test"
    assert str(captured["base_url"]) == "https://openrouter.ai/api/v1"


@pytest.mark.unit
def test_factory_openai_compatible_raises_when_api_key_env_missing(tmp_path: Path) -> None:
    """An explicit env-var secret source should fail before making an unauthenticated API call."""
    init_wiki(tmp_path)
    config_path = tmp_path / ".mdwiki" / "config.toml"
    config_path.write_text(
        '[llm]\nprovider = "openai-compatible"\nmodel = "minimax/minimax-m3"\n'
        '[llm.openai_compatible]\nbase_url = "https://openrouter.ai/api/v1"\n'
        'api_key_env = "OPENROUTER_API_KEY_NOT_SET"\n'
    )

    with pytest.raises(ValueError, match="OPENROUTER_API_KEY_NOT_SET"):
        build_provider_from_config(tmp_path)


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


@pytest.mark.unit
def test_factory_returns_session_provider_with_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``provider = "session"`` needs no API key and defaults to the claude CLI."""
    from mdwiki.llm.session import SessionProvider

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    init_wiki(tmp_path)
    (tmp_path / ".mdwiki" / "config.toml").write_text('[llm]\nprovider = "session"\nmodel = "claude-sonnet-4-6"\n')

    provider = build_provider_from_config(tmp_path)

    assert isinstance(provider, SessionProvider)
    assert provider.cli == "claude"
    assert provider.model == "sonnet"


@pytest.mark.unit
def test_factory_reads_the_session_config_block(tmp_path: Path) -> None:
    """Every ``[llm.session]`` key reaches the provider."""
    from mdwiki.llm.session import SessionProvider

    init_wiki(tmp_path)
    (tmp_path / ".mdwiki" / "config.toml").write_text(
        '[llm]\nprovider = "session"\n'
        '[llm.session]\ncli = "codex"\nmodel = "gpt-5"\ntimeout = 90.0\nmax_output_tokens = 8000\n'
        'verify_subscription = false\nstrip_env = ["MY_TOKEN"]\n'
    )

    provider = build_provider_from_config(tmp_path)

    assert isinstance(provider, SessionProvider)
    assert provider.cli == "codex"
    assert provider.model == "gpt-5"
    assert provider._timeout == 90.0
    assert provider._max_output_tokens == 8000
    assert provider._verify_subscription is False
    assert provider._strip_env == ("MY_TOKEN",)


@pytest.mark.unit
def test_factory_builds_custom_session_command(tmp_path: Path) -> None:
    from mdwiki.llm.session import SessionProvider

    init_wiki(tmp_path)
    (tmp_path / ".mdwiki" / "config.toml").write_text(
        '[llm]\nprovider = "session"\n[llm.session]\ncli = "custom"\ncommand = ["my-agent", "--system", "{system_file}"]\n'
    )

    provider = build_provider_from_config(tmp_path)

    assert isinstance(provider, SessionProvider)
    assert provider._command == ["my-agent", "--system", "{system_file}"]


@pytest.mark.unit
def test_factory_rejects_unknown_session_cli_naming_the_config_key(tmp_path: Path) -> None:
    init_wiki(tmp_path)
    (tmp_path / ".mdwiki" / "config.toml").write_text('[llm]\nprovider = "session"\n[llm.session]\ncli = "gemini"\n')

    with pytest.raises(ValueError, match=r"\[llm\.session\]\.cli") as excinfo:
        build_provider_from_config(tmp_path)

    assert "config.toml" in str(excinfo.value)


@pytest.mark.unit
def test_factory_rejects_custom_session_without_command(tmp_path: Path) -> None:
    init_wiki(tmp_path)
    (tmp_path / ".mdwiki" / "config.toml").write_text('[llm]\nprovider = "session"\n[llm.session]\ncli = "custom"\n')

    with pytest.raises(ValueError, match=r"\[llm\.session\]\.command"):
        build_provider_from_config(tmp_path)


@pytest.mark.unit
def test_provider_env_override_wins_over_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``MDWIKI_PROVIDER`` lets one invocation run in session mode without editing config."""
    from mdwiki.llm.session import SessionProvider

    init_wiki(tmp_path)  # default config: provider = "anthropic"
    monkeypatch.setenv("MDWIKI_PROVIDER", "session")
    monkeypatch.setenv("MDWIKI_SESSION_CLI", "codex")
    monkeypatch.setenv("MDWIKI_SESSION_MODEL", "gpt-5")

    provider = build_provider_from_config(tmp_path)

    assert isinstance(provider, SessionProvider)
    assert provider.cli == "codex"
    assert provider.model == "gpt-5"


@pytest.mark.unit
def test_provider_env_override_can_select_an_api_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The override is symmetric: a session-mode wiki can be run against the API for one call."""
    from mdwiki.llm.anthropic import AnthropicProvider

    init_wiki(tmp_path)
    (tmp_path / ".mdwiki" / "config.toml").write_text('[llm]\nprovider = "session"\nmodel = "claude-sonnet-4-6"\n')
    monkeypatch.setenv("MDWIKI_PROVIDER", "anthropic")

    provider = build_provider_from_config(tmp_path)

    assert isinstance(provider, AnthropicProvider)


@pytest.mark.unit
def test_unknown_provider_override_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    init_wiki(tmp_path)
    monkeypatch.setenv("MDWIKI_PROVIDER", "nonsense")

    with pytest.raises(UnknownProviderError, match="MDWIKI_PROVIDER"):
        build_provider_from_config(tmp_path)


@pytest.mark.unit
def test_session_cli_override_without_session_provider_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Asking for an agent CLI while the run would bill an API is a mistake, not a no-op."""
    init_wiki(tmp_path)
    (tmp_path / ".mdwiki" / "config.toml").write_text('[llm]\nprovider = "anthropic"\nmodel = "claude-sonnet-4-6"\n')
    monkeypatch.setenv("MDWIKI_SESSION_CLI", "codex")

    with pytest.raises(UnknownProviderError, match="MDWIKI_SESSION_CLI") as excinfo:
        build_provider_from_config(tmp_path)

    assert "MDWIKI_PROVIDER=session" in str(excinfo.value)


@pytest.mark.unit
def test_factory_passes_accepted_auth_methods_and_keep_env(tmp_path: Path) -> None:
    init_wiki(tmp_path)
    (tmp_path / ".mdwiki" / "config.toml").write_text(
        '[llm]\nprovider = "session"\n[llm.session]\naccepted_auth_methods = ["claude.ai", "oauth_token"]\nkeep_env = ["AWS_PROFILE"]\n'
    )

    provider = build_provider_from_config(tmp_path)

    assert provider._accepted_auth_methods == ("claude.ai", "oauth_token")  # type: ignore[attr-defined]
    assert provider._keep_env == ("AWS_PROFILE",)  # type: ignore[attr-defined]


@pytest.mark.unit
def test_init_under_a_provider_override_writes_it_to_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``mdwiki --provider session init`` must not leave a wiki that bills the API on its next command."""
    import tomllib

    monkeypatch.setenv("MDWIKI_PROVIDER", "session")
    monkeypatch.setenv("MDWIKI_SESSION_CLI", "codex")
    (tmp_path / "a.md").write_text("# A")

    init_wiki(tmp_path)

    config = tomllib.loads((tmp_path / ".mdwiki" / "config.toml").read_text())
    assert config["llm"]["provider"] == "session"
    assert config["llm"]["session"]["cli"] == "codex"


@pytest.mark.unit
def test_init_without_an_override_keeps_the_default_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import tomllib

    monkeypatch.delenv("MDWIKI_PROVIDER", raising=False)
    monkeypatch.delenv("MDWIKI_SESSION_CLI", raising=False)

    init_wiki(tmp_path)

    config = tomllib.loads((tmp_path / ".mdwiki" / "config.toml").read_text())
    assert config["llm"]["provider"] == "anthropic"
    assert "session" not in config["llm"]

"""LLM provider seam — factory turns ``[llm]`` config into a concrete ``Provider``.

v1.0.0 shipped one provider; v1.1.0 Phase 7 adds ``openai-compatible`` for vLLM,
llama.cpp, OpenRouter, Together, etc. Adding a new provider is config-only for
end users: set ``provider = "openai-compatible"`` in ``[llm]`` plus a
``[llm.openai_compatible]`` block with ``base_url``.

``provider = "session"`` runs completions through a local agent CLI on the
user's subscription instead of a paid API; see ``mdwiki.llm.session``. The
configured provider can be overridden for one process with ``MDWIKI_PROVIDER``
(and, for session mode, ``MDWIKI_SESSION_CLI`` / ``MDWIKI_SESSION_MODEL``).
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from mdwiki.discover import WIKI_DIR_NAME
from mdwiki.llm.anthropic import AnthropicProvider
from mdwiki.llm.base import CompleteResult, Message, PingResult, Provider
from mdwiki.llm.openai_compatible import OpenAICompatibleProvider
from mdwiki.llm.session import DEFAULT_ACCEPTED_AUTH_METHODS, KNOWN_SESSION_CLIS, SessionCliError, SessionProvider

__all__ = [
    "AnthropicProvider",
    "CompleteResult",
    "Message",
    "OpenAICompatibleProvider",
    "PROVIDER_ENV_VAR",
    "PingResult",
    "Provider",
    "SESSION_CLI_ENV_VAR",
    "SESSION_MODEL_ENV_VAR",
    "SessionCliError",
    "SessionProvider",
    "UnknownProviderError",
    "build_provider_from_config",
    "provider_override_for_new_wiki",
]

KNOWN_PROVIDERS: dict[str, type[Provider]] = {
    "anthropic": AnthropicProvider,
    "openai-compatible": OpenAICompatibleProvider,
    "session": SessionProvider,
}

# Per-process overrides. The CLI's ``--provider`` / ``--session-cli`` flags set
# these for one invocation; they can also be exported in a shell or a cron job.
PROVIDER_ENV_VAR: str = "MDWIKI_PROVIDER"
SESSION_CLI_ENV_VAR: str = "MDWIKI_SESSION_CLI"
SESSION_MODEL_ENV_VAR: str = "MDWIKI_SESSION_MODEL"


class UnknownProviderError(ValueError):
    """Raised when ``config.toml`` requests a provider mdwiki doesn't know about."""


def build_provider_from_config(wiki_root: Path) -> Provider:
    """Read ``<wiki_root>/.mdwiki/config.toml`` and return the matching ``Provider`` instance.

    Provider-specific kwargs come from a per-provider config block (e.g.
    ``[llm.openai_compatible]``); each provider class chooses what it consumes.

    Raises
    ------
    UnknownProviderError
        If ``[llm].provider`` names a backend mdwiki doesn't ship a provider for.
    ValueError
        If the named provider's config block is missing required keys
        (e.g. ``base_url`` for openai-compatible).
    """
    config_path = wiki_root / WIKI_DIR_NAME / "config.toml"
    config = tomllib.loads(config_path.read_text())
    llm_section = config.get("llm", {})
    provider_name = llm_section.get("provider", "anthropic")
    model = llm_section.get("model", "claude-sonnet-4-6")

    override = os.environ.get(PROVIDER_ENV_VAR, "").strip()
    origin = f"llm.provider {provider_name!r} in {config_path}"
    if override:
        provider_name = override
        origin = f"provider {override!r} from {PROVIDER_ENV_VAR} (or --provider)"

    provider_cls = KNOWN_PROVIDERS.get(provider_name)
    if provider_cls is None:
        known = ", ".join(sorted(KNOWN_PROVIDERS)) or "(none yet)"
        raise UnknownProviderError(f"Unknown {origin}. Known providers: {known}.")

    session_cli_override = os.environ.get(SESSION_CLI_ENV_VAR, "").strip()
    if session_cli_override and provider_name != "session":
        # Asking for an agent CLI while the run would bill an API is a mistake, not a no-op.
        raise UnknownProviderError(
            f"{SESSION_CLI_ENV_VAR}={session_cli_override!r} (or --session-cli) is set, but the provider is "
            f"{provider_name!r}, which would not use an agent CLI. Set {PROVIDER_ENV_VAR}=session "
            f"(or --provider session), or unset {SESSION_CLI_ENV_VAR}."
        )

    if provider_name == "openai-compatible":
        return _build_openai_compatible(model=model, llm_section=llm_section, config_path=config_path)

    if provider_name == "session":
        return _build_session(llm_section=llm_section, config_path=config_path)

    return provider_cls(model=model)


def provider_override_for_new_wiki() -> dict[str, Any]:
    """Return the ``[llm]`` overlay a wiki created under a provider override should be written with.

    Without this, ``mdwiki --provider session init`` would bootstrap through the
    agent CLI and then leave a config that bills the API on the next command.
    """
    provider_name = os.environ.get(PROVIDER_ENV_VAR, "").strip()
    if not provider_name or provider_name not in KNOWN_PROVIDERS:
        return {}
    overlay: dict[str, Any] = {"provider": provider_name}
    if provider_name == "session":
        session: dict[str, Any] = {}
        cli = os.environ.get(SESSION_CLI_ENV_VAR, "").strip()
        model = os.environ.get(SESSION_MODEL_ENV_VAR, "").strip()
        if cli:
            session["cli"] = cli
        if model:
            session["model"] = model
        if session:
            overlay["session"] = session
    return overlay


def _build_openai_compatible(
    *,
    model: str,
    llm_section: dict[str, Any],
    config_path: Path,
) -> OpenAICompatibleProvider:
    """Pull base_url / api_key / timeout / vision_capable from the [llm.openai_compatible] block."""
    block = llm_section.get("openai_compatible", {}) or {}
    base_url = block.get("base_url")
    if not base_url:
        raise ValueError(
            f"provider 'openai-compatible' in {config_path} requires "
            "[llm.openai_compatible].base_url (e.g. 'http://localhost:8000/v1')."
        )
    api_key = block.get("api_key")
    api_key_env = block.get("api_key_env")
    if api_key is None and api_key_env:
        api_key = os.environ.get(str(api_key_env))
        if not api_key:
            raise ValueError(
                f"provider 'openai-compatible' in {config_path} reads "
                f"[llm.openai_compatible].api_key_env={api_key_env!r}, but that environment variable is not set."
            )
    return OpenAICompatibleProvider(
        model=model,
        base_url=base_url,
        api_key=api_key,
        timeout=float(block.get("timeout", 120.0)),
        vision_capable=bool(block.get("vision_capable", False)),
    )


def _build_session(*, llm_section: dict[str, Any], config_path: Path) -> SessionProvider:
    """Pull cli / model / command / timeout and friends from the [llm.session] block.

    ``[llm].model`` is deliberately not reused: it names an API model id, which an
    agent CLI may not accept. Session mode takes its model from
    ``[llm.session].model`` or ``MDWIKI_SESSION_MODEL``, else the CLI's default.
    """
    block = llm_section.get("session", {}) or {}
    cli = os.environ.get(SESSION_CLI_ENV_VAR, "").strip() or str(block.get("cli", "claude"))
    if cli not in KNOWN_SESSION_CLIS:
        known = ", ".join(KNOWN_SESSION_CLIS)
        raise ValueError(f"provider 'session' in {config_path} has [llm.session].cli={cli!r}. Known values: {known}.")
    command = block.get("command") or []
    if cli == "custom" and not command:
        raise ValueError(
            f"provider 'session' in {config_path} with cli = 'custom' requires "
            "[llm.session].command (e.g. ['my-agent', '--system', '{system_file}'])."
        )
    model = os.environ.get(SESSION_MODEL_ENV_VAR, "").strip() or block.get("model") or None
    return SessionProvider(
        cli=cli,
        model=str(model) if model else None,
        command=[str(token) for token in command],
        timeout=float(block.get("timeout", 1500.0)),
        max_output_tokens=int(block.get("max_output_tokens", 32000)),
        verify_subscription=bool(block.get("verify_subscription", True)),
        accepted_auth_methods=tuple(str(name) for name in block.get("accepted_auth_methods", DEFAULT_ACCEPTED_AUTH_METHODS)),
        strip_env=tuple(str(name) for name in block.get("strip_env", [])),
        keep_env=tuple(str(name) for name in block.get("keep_env", [])),
    )

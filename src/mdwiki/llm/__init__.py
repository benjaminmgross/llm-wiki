"""LLM provider seam — factory turns ``[llm]`` config into a concrete ``Provider``.

v1.0.0 shipped one provider; v1.1.0 Phase 7 adds ``openai-compatible`` for vLLM,
llama.cpp, OpenRouter, Together, etc. Adding a new provider is config-only for
end users: set ``provider = "openai-compatible"`` in ``[llm]`` plus a
``[llm.openai_compatible]`` block with ``base_url``.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from mdwiki.discover import WIKI_DIR_NAME
from mdwiki.llm.anthropic import AnthropicProvider
from mdwiki.llm.base import CompleteResult, Message, PingResult, Provider
from mdwiki.llm.openai_compatible import OpenAICompatibleProvider

__all__ = [
    "AnthropicProvider",
    "CompleteResult",
    "Message",
    "OpenAICompatibleProvider",
    "PingResult",
    "Provider",
    "UnknownProviderError",
    "build_provider_from_config",
]

KNOWN_PROVIDERS: dict[str, type[Provider]] = {
    "anthropic": AnthropicProvider,
    "openai-compatible": OpenAICompatibleProvider,
}


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

    provider_cls = KNOWN_PROVIDERS.get(provider_name)
    if provider_cls is None:
        known = ", ".join(sorted(KNOWN_PROVIDERS)) or "(none yet)"
        raise UnknownProviderError(
            f"Unknown llm.provider {provider_name!r} in {config_path}. Known providers: {known}."
        )

    if provider_name == "openai-compatible":
        return _build_openai_compatible(model=model, llm_section=llm_section, config_path=config_path)

    return provider_cls(model=model)


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
    return OpenAICompatibleProvider(
        model=model,
        base_url=base_url,
        api_key=block.get("api_key"),
        timeout=float(block.get("timeout", 120.0)),
        vision_capable=bool(block.get("vision_capable", False)),
    )

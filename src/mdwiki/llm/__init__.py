"""LLM provider seam — factory turns ``[llm]`` config into a concrete ``Provider``.

v1.0.0 ships with one provider; the seam exists so Day-2 local providers (Qwen,
Kimi) can land as new files in this folder without touching ingest, query, or lint.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from mdwiki.discover import WIKI_DIR_NAME
from mdwiki.llm.anthropic import AnthropicProvider
from mdwiki.llm.base import CompleteResult, Message, PingResult, Provider

__all__ = [
    "AnthropicProvider",
    "CompleteResult",
    "Message",
    "PingResult",
    "Provider",
    "UnknownProviderError",
    "build_provider_from_config",
]

KNOWN_PROVIDERS: dict[str, type[Provider]] = {
    "anthropic": AnthropicProvider,
}


class UnknownProviderError(ValueError):
    """Raised when ``config.toml`` requests a provider mdwiki doesn't know about."""


def build_provider_from_config(wiki_root: Path) -> Provider:
    """Read ``<wiki_root>/.mdwiki/config.toml`` and return the matching ``Provider`` instance.

    Parameters
    ----------
    wiki_root : Path
        Directory containing ``.mdwiki/``.

    Raises
    ------
    UnknownProviderError
        If ``[llm].provider`` names a backend that is not yet supported.
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
    return provider_cls(model=model)

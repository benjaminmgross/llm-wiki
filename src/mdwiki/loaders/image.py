"""Image loader — delegates raster-image extraction to the configured provider's vision API.

Opt-in by default (vision calls run ~$0.10–0.50 per image). The user enables it
via ``[loaders.image].enabled = true`` in ``.mdwiki/config.toml``; without that
flag, ``get_loader_for(image.png)`` raises ``UnsupportedFiletypeError`` so the
file is silently skipped during init/ingest.
"""

from __future__ import annotations

from pathlib import Path

from mdwiki.llm.base import Provider
from mdwiki.loaders.base import Loader

_IMAGE_SUFFIXES: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})


class ImageLoader(Loader):
    """Convert raster images to markdown via the provider's ``describe_image`` method."""

    name: str = "ImageLoader"

    def __init__(self, provider: Provider | None = None) -> None:
        self._provider = provider

    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() in _IMAGE_SUFFIXES

    def load_to_markdown(self, path: Path) -> str:
        if self._provider is None:
            raise RuntimeError(
                "ImageLoader was instantiated without a provider. "
                "Construct via ImageLoader(provider=AnthropicProvider(...)) "
                "or rely on the config-aware loaders.build_registry factory."
            )
        return self._provider.describe_image(path)

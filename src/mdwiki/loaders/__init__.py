"""Loader registry — single entry point for converting any source file to markdown text.

``get_loader_for(path)`` walks up from ``path`` to find a ``.mdwiki/`` directory,
reads its ``config.toml``, and builds a registry that respects per-loader opt-in
flags: ``[loaders.image].enabled`` and ``[loaders.pdf].vision_fallback``. Without
a wiki ancestor (e.g. tests using ``tmp_path``), defaults apply (image disabled,
pdf vision fallback disabled).

Callers needing a registry with a specific provider injected (init / ingest paths)
should use ``build_registry(config=..., provider=...)`` directly rather than
``get_loader_for``.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from mdwiki.llm.base import Provider
from mdwiki.loaders.base import Loader
from mdwiki.loaders.code import CodeLoader
from mdwiki.loaders.csv_loader import CsvLoader
from mdwiki.loaders.docx import DocxLoader
from mdwiki.loaders.html import HtmlLoader
from mdwiki.loaders.image import ImageLoader
from mdwiki.loaders.markdown import MarkdownLoader
from mdwiki.loaders.pdf import PdfLoader
from mdwiki.loaders.text import TextLoader

__all__ = [
    "CodeLoader",
    "CsvLoader",
    "DocxLoader",
    "HtmlLoader",
    "ImageLoader",
    "Loader",
    "MarkdownLoader",
    "PdfLoader",
    "TextLoader",
    "UnsupportedFiletypeError",
    "build_registry",
    "get_loader_for",
]


class UnsupportedFiletypeError(Exception):
    """Raised by ``get_loader_for`` when no registered loader claims the path."""


def build_registry(
    *,
    config: Mapping[str, Any] | None = None,
    provider: Provider | None = None,
) -> tuple[Loader, ...]:
    """Build a loader registry from optional config + provider.

    Parameters
    ----------
    config : Mapping[str, Any], optional
        Parsed ``config.toml``. ``[loaders.image].enabled`` and
        ``[loaders.pdf].vision_fallback`` are honored.
    provider : Provider, optional
        Vision-capable provider. Required only if image-loading or PDF vision
        fallback is enabled in ``config``.

    Returns
    -------
    tuple[Loader, ...]
        Registry in lookup order. Markdown / code / csv / docx / pdf / html / text
        come first (deterministic, fast); ``ImageLoader`` is appended only if
        the config explicitly enables it.
    """
    loaders_cfg = (config or {}).get("loaders", {})
    image_enabled = bool(loaders_cfg.get("image", {}).get("enabled", False))
    pdf_vision = bool(loaders_cfg.get("pdf", {}).get("vision_fallback", False))

    loaders: list[Loader] = [
        MarkdownLoader(),
        CodeLoader(),
        CsvLoader(),
        DocxLoader(),
        PdfLoader(provider=provider if pdf_vision else None, vision_fallback=pdf_vision),
        HtmlLoader(),
        TextLoader(),
    ]
    if image_enabled:
        loaders.append(ImageLoader(provider=provider))
    return tuple(loaders)


def get_loader_for(path: Path) -> Loader:
    """Return the first registered loader claiming ``path``.

    Walks up from ``path`` looking for ``.mdwiki/config.toml``; uses defaults
    (image disabled, pdf vision_fallback disabled) when no wiki ancestor exists.
    The returned ``ImageLoader`` (when enabled) has no provider injected — callers
    that need to actually call ``load_to_markdown`` on an image must use
    ``build_registry(provider=...)`` instead.

    Raises
    ------
    UnsupportedFiletypeError
        No registered loader claims this path's extension.
    """
    config = _try_load_config_near(path)
    registry = build_registry(config=config)
    for loader in registry:
        if loader.can_handle(path):
            return loader
    raise UnsupportedFiletypeError(f"No loader registered for: {path}")


def _try_load_config_near(path: Path) -> dict[str, Any]:
    """Walk up from ``path`` to find ``.mdwiki/config.toml``; return parsed config or {}."""
    # Lazy import: avoids a hard cycle through mdwiki.discover at module load.
    from mdwiki.discover import WikiNotFound, find_wiki

    search_from = path if path.is_dir() else path.parent
    if not search_from.exists():
        return {}
    try:
        wiki_root = find_wiki(search_from)
    except WikiNotFound:
        return {}
    config_path = wiki_root / ".mdwiki" / "config.toml"
    if not config_path.is_file():
        return {}
    try:
        return tomllib.loads(config_path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return {}

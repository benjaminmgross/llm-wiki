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
from mdwiki.loaders.transcript import TranscriptLoader

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
    "TranscriptLoader",
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

    # TranscriptLoader is listed BEFORE MarkdownLoader so a ``*-transcript.md``
    # file routes to TranscriptLoader (which promotes Fathom-style speaker
    # blocks to H2) instead of falling through to passthrough markdown
    # handling. ``.vtt`` and ``.srt`` are exclusively TranscriptLoader's;
    # plain ``.md`` without a transcript filename hint still goes to
    # MarkdownLoader.
    loaders: list[Loader] = [
        TranscriptLoader(),
        MarkdownLoader(),
        CodeLoader(),
        CsvLoader(),
        DocxLoader(),
        PdfLoader(provider=provider if pdf_vision else None, vision_fallback=pdf_vision),
        HtmlLoader(),
        TextLoader(),
    ]
    # Only register ImageLoader when both the config opts in AND a real provider
    # is available. Without a provider the loader's ``can_handle`` would still
    # claim the file but ``load_to_markdown`` would blow up at call time —
    # surprising behavior we'd rather avoid by leaving images "unsupported"
    # until the user wires up a vision-capable provider.
    if image_enabled and provider is not None:
        loaders.append(ImageLoader(provider=provider))
    return tuple(loaders)


def get_loader_for(path: Path) -> Loader:
    """Return the first registered loader claiming ``path``.

    Walks up from ``path`` looking for ``.mdwiki/config.toml``; uses defaults
    (image disabled, pdf vision_fallback disabled) when no wiki ancestor exists.
    No provider is injected here, so ``ImageLoader`` is NOT registered even
    when ``[loaders.image].enabled = true`` in config — image inputs route to
    ``UnsupportedFiletypeError`` until the caller switches to
    ``build_registry(config=..., provider=...)`` with a vision-capable provider.

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

"""Loader registry — single entry point for converting any source file to markdown text.

Callers use ``get_loader_for(path)`` to resolve a path to its loader. The registry
is order-sensitive (first matching ``can_handle`` wins), giving more specific
loaders a chance to claim before generic ones.

v1.1.0 registers only ``MarkdownLoader``; subsequent phases append ``TextLoader``,
``CodeLoader``, ``CsvLoader``, ``PdfLoader``, ``DocxLoader``, ``HtmlLoader``, and
``ImageLoader`` (the last one opt-in via config).
"""

from __future__ import annotations

from pathlib import Path

from mdwiki.loaders.base import Loader
from mdwiki.loaders.markdown import MarkdownLoader

__all__ = ["Loader", "MarkdownLoader", "UnsupportedFiletypeError", "get_loader_for"]


class UnsupportedFiletypeError(Exception):
    """Raised by ``get_loader_for`` when no registered loader claims the path."""


_REGISTRY: tuple[Loader, ...] = (MarkdownLoader(),)


def get_loader_for(path: Path) -> Loader:
    """Return the first registered loader whose ``can_handle(path)`` is ``True``.

    Parameters
    ----------
    path : Path
        Source file to load. Only the extension is inspected — no I/O.

    Raises
    ------
    UnsupportedFiletypeError
        No registered loader claims this path's extension.
    """
    for loader in _REGISTRY:
        if loader.can_handle(path):
            return loader
    raise UnsupportedFiletypeError(f"No loader registered for: {path}")

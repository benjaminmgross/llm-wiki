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
from mdwiki.loaders.code import CodeLoader
from mdwiki.loaders.csv_loader import CsvLoader
from mdwiki.loaders.docx import DocxLoader
from mdwiki.loaders.html import HtmlLoader
from mdwiki.loaders.markdown import MarkdownLoader
from mdwiki.loaders.pdf import PdfLoader
from mdwiki.loaders.text import TextLoader

__all__ = [
    "CodeLoader",
    "CsvLoader",
    "DocxLoader",
    "HtmlLoader",
    "Loader",
    "MarkdownLoader",
    "PdfLoader",
    "TextLoader",
    "UnsupportedFiletypeError",
    "get_loader_for",
]


class UnsupportedFiletypeError(Exception):
    """Raised by ``get_loader_for`` when no registered loader claims the path."""


# Registry order: markdown first (most specific use case), then code (specific
# extensions), then csv (specific extensions), then docx/pdf (specific binary
# formats), then text (generic fallback for remaining text-like files). Each
# loader's can_handle is extension-bounded, so order only matters if two loaders
# ever claim the same extension — they don't.
_REGISTRY: tuple[Loader, ...] = (
    MarkdownLoader(),
    CodeLoader(),
    CsvLoader(),
    DocxLoader(),
    PdfLoader(),
    HtmlLoader(),
    TextLoader(),
)


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

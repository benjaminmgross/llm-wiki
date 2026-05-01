"""Loader abstract base class — the seam that lets v1.1.0 ingest non-markdown filetypes.

A ``Loader`` converts a single source file (md, txt, csv, pdf, docx, html, image, ...)
into a markdown string. ``init_wiki`` writes the result to ``raw/<hash>-<slug>.md``,
giving the chunker a uniform markdown surface regardless of original filetype.

Subclasses implement two methods: ``can_handle(path)`` for the registry's path-only
extension check, and ``load_to_markdown(path)`` to produce the converted text.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class Loader(ABC):
    """Abstract base for a filetype loader.

    Subclasses must implement ``can_handle`` (a pure path check) and
    ``load_to_markdown`` (the actual conversion). The class-level ``name``
    attribute is recorded in ``raw/.sources.json`` for diagnostic visibility.
    """

    name: str = ""

    @abstractmethod
    def can_handle(self, path: Path) -> bool:
        """Return ``True`` if this loader claims ``path`` based on extension alone.

        Implementations must be pure: no filesystem or network access. The
        registry calls ``can_handle`` once per candidate path during
        ``get_loader_for``; touching disk inside it would be wasteful.
        """

    @abstractmethod
    def load_to_markdown(self, path: Path) -> str:
        """Read ``path`` and return its content as markdown text.

        Markdown loaders pass content through unchanged; non-markdown loaders
        convert (text → fenced block, csv → table, html → markdownify, etc.).
        Returning an empty string signals "could not extract content"; callers
        decide whether to skip or fall back to a different loader.
        """

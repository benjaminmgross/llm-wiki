"""Markdown passthrough loader — the trivial baseline implementation."""

from __future__ import annotations

from pathlib import Path

from mdwiki.loaders.base import Loader

_MARKDOWN_SUFFIXES: frozenset[str] = frozenset({".md", ".markdown"})


class MarkdownLoader(Loader):
    """Passthrough for ``.md`` and ``.markdown`` files."""

    name: str = "MarkdownLoader"

    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() in _MARKDOWN_SUFFIXES

    def load_to_markdown(self, path: Path) -> str:
        return path.read_text()

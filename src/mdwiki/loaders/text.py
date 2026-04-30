"""Plain-text loader — wraps ``.txt`` / ``.log`` / ``.rst`` content in a fenced block.

The fenced block keeps whitespace, indentation, and line structure intact when the
chunker (which chunks on H2 headers) and downstream LLM see the file. Without the
fence, plain text would be reflowed by markdown renderers and structure would be lost.
"""

from __future__ import annotations

from pathlib import Path

from mdwiki.loaders.base import Loader

_TEXT_SUFFIXES: frozenset[str] = frozenset({".txt", ".log", ".rst"})


class TextLoader(Loader):
    """Wrap plain-text content in a markdown fenced block."""

    name: str = "TextLoader"

    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() in _TEXT_SUFFIXES

    def load_to_markdown(self, path: Path) -> str:
        body = path.read_text()
        if not body.endswith("\n"):
            body += "\n"
        return f"```\n{body}```\n"

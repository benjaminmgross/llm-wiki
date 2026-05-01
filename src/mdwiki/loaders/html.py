"""HTML loader — converts ``.html`` / ``.htm`` to markdown via ``markdownify``.

``<script>``, ``<style>``, and ``<noscript>`` blocks are stripped before conversion:
they're never useful context for the LLM and embedding raw JS/CSS in raw/ would
pollute downstream chunking with selectors and minified payloads.
"""

from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup
from markdownify import markdownify

from mdwiki.loaders.base import Loader

_HTML_SUFFIXES: frozenset[str] = frozenset({".html", ".htm"})
_DROP_TAGS: tuple[str, ...] = ("script", "style", "noscript")


class HtmlLoader(Loader):
    """Convert HTML to markdown after stripping script/style/noscript blocks."""

    name: str = "HtmlLoader"

    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() in _HTML_SUFFIXES

    def load_to_markdown(self, path: Path) -> str:
        soup = BeautifulSoup(path.read_text(), "html.parser")
        for tag in soup(_DROP_TAGS):
            tag.decompose()
        # heading_style="ATX" produces "# Heading" (matches markdown.md convention used
        # everywhere else in mdwiki); the default "underline" style breaks our chunker
        # which splits on H2 ATX headers.
        return markdownify(str(soup), heading_style="ATX")

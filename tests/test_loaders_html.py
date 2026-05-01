"""Tests for ``HtmlLoader`` — converts ``.html``/``.htm`` to clean markdown.

``<script>``, ``<style>``, and ``<noscript>`` tags are stripped before conversion
both for safety (no executable content surviving into raw/) and for content
cleanliness (CSS/JS selectors aren't useful context for the LLM).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mdwiki.loaders import get_loader_for
from mdwiki.loaders.html import HtmlLoader


@pytest.mark.unit
def test_html_loader_strips_tags_and_preserves_structure(tmp_path: Path) -> None:
    """Headings become ``#``-prefixed; paragraphs/links round-trip; no raw tags survive."""
    src = tmp_path / "page.html"
    src.write_text("<h1>Title</h1><p>Body text with <a href='x'>link</a>.</p>")
    md = HtmlLoader().load_to_markdown(src)
    assert "# Title" in md
    assert "Body text" in md
    assert "[link](x)" in md
    assert "<h1>" not in md
    assert "<p>" not in md


@pytest.mark.unit
def test_html_loader_drops_script_and_style(tmp_path: Path) -> None:
    """``<script>`` / ``<style>`` content is removed; surrounding text survives."""
    src = tmp_path / "page.html"
    src.write_text(
        "<script>alert(1)</script>"
        "<style>x{color:red}</style>"
        "<noscript>Please enable JS</noscript>"
        "<p>Content</p>"
    )
    md = HtmlLoader().load_to_markdown(src)
    assert "alert" not in md
    assert "color:red" not in md
    assert "Please enable JS" not in md
    assert "Content" in md


@pytest.mark.unit
def test_html_loader_handles_full_html_document(tmp_path: Path) -> None:
    """A full ``<html><head>...</head><body>...</body></html>`` round-trips cleanly."""
    src = tmp_path / "page.html"
    src.write_text(
        "<!DOCTYPE html><html><head><title>T</title>"
        "<style>body{margin:0}</style></head>"
        "<body><h1>Heading</h1><p>Paragraph one.</p>"
        "<ul><li>item one</li><li>item two</li></ul></body></html>"
    )
    md = HtmlLoader().load_to_markdown(src)
    assert "# Heading" in md
    assert "Paragraph one." in md
    assert "item one" in md
    assert "item two" in md
    assert "margin:0" not in md  # style stripped


@pytest.mark.unit
def test_html_loader_routed_for_html_and_htm(tmp_path: Path) -> None:
    """Both ``.html`` and ``.htm`` route to ``HtmlLoader``."""
    for ext in (".html", ".htm"):
        src = tmp_path / f"page{ext}"
        src.write_text("<p>x</p>")
        loader = get_loader_for(src)
        assert isinstance(loader, HtmlLoader), f"{ext} did not route to HtmlLoader"


@pytest.mark.unit
def test_html_loader_can_handle_html_only() -> None:
    loader = HtmlLoader()
    assert loader.can_handle(Path("a.html")) is True
    assert loader.can_handle(Path("a.htm")) is True
    assert loader.can_handle(Path("a.xml")) is False
    assert loader.can_handle(Path("a.md")) is False

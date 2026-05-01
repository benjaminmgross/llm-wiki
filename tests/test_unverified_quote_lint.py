"""Tests for the ``[unverified-quote]`` lint check (Phase 5).

When the project opts into lenient quote-anchor mode (a future config flag),
unverified claims survive in pages with a ``[unverified-quote]`` marker.
``mdwiki lint`` flags any wiki page containing this marker so the user can
remove or replace the unverified content.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mdwiki.init import init_wiki
from mdwiki.lint import lint_wiki


@pytest.mark.unit
def test_lint_flags_page_with_unverified_quote_marker(tmp_path: Path) -> None:
    """A page containing the ``[unverified-quote]`` marker yields a lint finding."""
    init_wiki(tmp_path, profile="working-dir")
    page = tmp_path / "wiki" / "concepts" / "foo.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        "# Foo\n\nThis is a verified claim with a real citation.\n\n"
        "This claim is [unverified-quote] and the LLM couldn't anchor it to the source.\n"
    )
    other = tmp_path / "wiki" / "concepts" / "bar.md"
    other.write_text("# Bar\n\nNo unverified content here.\n")

    report = lint_wiki(tmp_path)

    findings = [f for f in report.findings if f.kind == "unverified-quote"]
    assert len(findings) == 1
    assert findings[0].page_path == "wiki/concepts/foo.md"


@pytest.mark.unit
def test_lint_does_not_flag_pages_without_marker(tmp_path: Path) -> None:
    """A wiki with no ``[unverified-quote]`` markers produces zero findings of that kind."""
    init_wiki(tmp_path, profile="working-dir")
    page = tmp_path / "wiki" / "concepts" / "foo.md"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text("# Foo\n\nFully verified content.\n")

    report = lint_wiki(tmp_path)

    findings = [f for f in report.findings if f.kind == "unverified-quote"]
    assert findings == []


@pytest.mark.unit
def test_lint_flags_all_pages_with_marker(tmp_path: Path) -> None:
    """Multiple pages with the marker yield multiple findings."""
    init_wiki(tmp_path, profile="working-dir")
    for name in ("foo.md", "bar.md", "baz.md"):
        page = tmp_path / "wiki" / "concepts" / name
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(f"# {name}\n\nClaim [unverified-quote] in {name}.\n")

    report = lint_wiki(tmp_path)

    findings = [f for f in report.findings if f.kind == "unverified-quote"]
    assert len(findings) == 3

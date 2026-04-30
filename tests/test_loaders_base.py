"""Tests for ``mdwiki.loaders`` — Loader ABC, registry, and the markdown passthrough."""

from __future__ import annotations

from pathlib import Path

import pytest

from mdwiki.loaders import UnsupportedFiletypeError, get_loader_for
from mdwiki.loaders.base import Loader
from mdwiki.loaders.markdown import MarkdownLoader


@pytest.mark.unit
def test_markdown_loader_passthrough_round_trips_content(tmp_path: Path) -> None:
    """A registered ``MarkdownLoader`` hands back the file's text unchanged."""
    src = tmp_path / "x.md"
    src.write_text("# Hello\n\nbody")
    loader = get_loader_for(src)
    assert isinstance(loader, MarkdownLoader)
    assert loader.load_to_markdown(src) == "# Hello\n\nbody"


@pytest.mark.unit
def test_get_loader_for_unknown_extension_raises(tmp_path: Path) -> None:
    """``get_loader_for`` rejects extensions no registered loader claims."""
    src = tmp_path / "foo.weirdext"
    src.touch()
    with pytest.raises(UnsupportedFiletypeError):
        get_loader_for(src)


@pytest.mark.unit
def test_markdown_loader_handles_markdown_extension_too(tmp_path: Path) -> None:
    """``.markdown`` files route to ``MarkdownLoader`` (the long form is widely used)."""
    src = tmp_path / "y.markdown"
    src.write_text("body only")
    loader = get_loader_for(src)
    assert isinstance(loader, MarkdownLoader)
    assert loader.load_to_markdown(src) == "body only"


@pytest.mark.unit
def test_markdown_loader_can_handle_returns_true_for_md_only() -> None:
    """``can_handle`` is a pure path check — no filesystem access required."""
    loader = MarkdownLoader()
    assert loader.can_handle(Path("a.md")) is True
    assert loader.can_handle(Path("a.markdown")) is True
    assert loader.can_handle(Path("a.txt")) is False
    assert loader.can_handle(Path("a")) is False


@pytest.mark.unit
def test_loader_abc_cannot_be_instantiated() -> None:
    """``Loader`` itself is abstract — instantiation must fail."""
    with pytest.raises(TypeError):
        Loader()  # type: ignore[abstract]


@pytest.mark.unit
def test_unsupported_filetype_error_message_names_the_path(tmp_path: Path) -> None:
    """The error message includes the offending path so users can locate the bad file."""
    src = tmp_path / "weird.zzz"
    src.touch()
    with pytest.raises(UnsupportedFiletypeError, match="weird.zzz"):
        get_loader_for(src)

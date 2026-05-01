"""Tests for ``ImageLoader`` and the config-aware ``get_loader_for`` routing.

ImageLoader is opt-in (cost-sensitive — vision calls run ~$0.10–0.50 each).
Default config has ``[loaders.image].enabled = false`` so ``get_loader_for(*.png)``
raises ``UnsupportedFiletypeError`` until the user explicitly enables it in
``.mdwiki/config.toml``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomli_w

from mdwiki.loaders import UnsupportedFiletypeError, get_loader_for
from mdwiki.loaders.image import ImageLoader


@pytest.mark.unit
def test_image_loader_calls_vision_provider_and_returns_markdown(tmp_path: Path, mocker: object) -> None:
    """``ImageLoader`` delegates the actual extraction to ``provider.describe_image``."""
    src = tmp_path / "diagram.png"
    src.write_bytes(b"\x89PNG fake bytes for test")

    fake_provider = mocker.Mock()
    fake_provider.describe_image.return_value = "## Image: diagram.png\n\nA flow chart describing X."

    md = ImageLoader(provider=fake_provider).load_to_markdown(src)

    assert "Image: diagram.png" in md
    assert "flow chart" in md.lower()
    fake_provider.describe_image.assert_called_once_with(src)


@pytest.mark.unit
def test_image_loader_without_provider_raises_at_call_time(tmp_path: Path) -> None:
    """An ``ImageLoader`` instantiated without a provider raises a clear error on use."""
    src = tmp_path / "x.png"
    src.write_bytes(b"\x89PNG")
    with pytest.raises(RuntimeError, match="provider"):
        ImageLoader().load_to_markdown(src)


@pytest.mark.unit
def test_image_loader_can_handle_common_image_formats() -> None:
    """``can_handle`` returns True for png/jpg/jpeg/webp/gif."""
    loader = ImageLoader()
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".PNG", ".JPG"):
        assert loader.can_handle(Path(f"x{ext}")) is True, ext
    assert loader.can_handle(Path("x.svg")) is False  # SVG isn't a raster image — explicitly skip
    assert loader.can_handle(Path("x.md")) is False


@pytest.mark.unit
def test_get_loader_for_image_disabled_by_default(tmp_path: Path) -> None:
    """Without a wiki ancestor (and thus no config), images route to UnsupportedFiletypeError."""
    src = tmp_path / "x.png"
    src.touch()
    with pytest.raises(UnsupportedFiletypeError):
        get_loader_for(src)


@pytest.mark.unit
def test_get_loader_for_image_enabled_in_config_still_unsupported_without_provider(tmp_path: Path) -> None:
    """Even with [loaders.image].enabled = true, ``get_loader_for`` (no provider) keeps images unsupported.

    The factory only registers ``ImageLoader`` when both the config opts in AND
    a vision-capable provider is supplied. ``get_loader_for`` never injects a
    provider, so images stay UnsupportedFiletypeError — callers needing image
    loading must switch to ``build_registry(provider=...)``.
    """
    wiki_dir = tmp_path / ".mdwiki"
    wiki_dir.mkdir()
    config = {"loaders": {"image": {"enabled": True}}, "llm": {"provider": "anthropic", "model": "x"}}
    (wiki_dir / "config.toml").write_bytes(tomli_w.dumps(config).encode())

    src = tmp_path / "x.png"
    src.write_bytes(b"\x89PNG")
    with pytest.raises(UnsupportedFiletypeError):
        get_loader_for(src)


@pytest.mark.unit
def test_build_registry_image_enabled_with_provider_registers_image_loader(tmp_path: Path) -> None:
    """``build_registry(config=..., provider=fake)`` with image enabled registers a usable ImageLoader."""
    from unittest.mock import MagicMock

    from mdwiki.loaders import build_registry

    fake_provider = MagicMock()
    config = {"loaders": {"image": {"enabled": True}}}
    registry = build_registry(config=config, provider=fake_provider)
    image_loaders = [loader for loader in registry if isinstance(loader, ImageLoader)]
    assert len(image_loaders) == 1


@pytest.mark.unit
def test_get_loader_for_image_explicitly_disabled_via_config(tmp_path: Path) -> None:
    """A wiki with [loaders.image].enabled = false explicitly behaves like no config."""
    wiki_dir = tmp_path / ".mdwiki"
    wiki_dir.mkdir()
    config = {"loaders": {"image": {"enabled": False}}}
    (wiki_dir / "config.toml").write_bytes(tomli_w.dumps(config).encode())

    src = tmp_path / "x.png"
    src.touch()
    with pytest.raises(UnsupportedFiletypeError):
        get_loader_for(src)

"""Layered configuration: user-level defaults under the per-wiki ``.mdwiki/config.toml``.

``${XDG_CONFIG_HOME:-~/.config}/mdwiki/config.toml`` holds machine-wide
defaults (typically the provider block and an API key for an OpenAI-compatible
endpoint) so several wikis need not repeat them. The wiki file wins on every
leaf conflict; the user file only fills gaps. Profile knobs, pointer files,
and exclusions stay per-wiki by nature.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from mdwiki.discover import WIKI_DIR_NAME
from mdwiki.profiles import deep_merge

USER_CONFIG_DIR_NAME: str = "mdwiki"
USER_CONFIG_FILE_NAME: str = "config.toml"


def user_config_path() -> Path:
    """Return the user-level config path (``$XDG_CONFIG_HOME`` or ``~/.config``)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / USER_CONFIG_DIR_NAME / USER_CONFIG_FILE_NAME


def load_user_config() -> dict[str, Any]:
    """Parse the user-level file; ``{}`` when absent.

    Raises
    ------
    ValueError
        When the file exists but is not valid TOML (named so the user can fix it).
    """
    path = user_config_path()
    if not path.is_file():
        return {}
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"user-level mdwiki config.toml at {path} is not valid TOML: {exc}") from exc


def load_wiki_config(wiki_root: Path) -> dict[str, Any]:
    """Parse ``<wiki_root>/.mdwiki/config.toml`` only (no user layer)."""
    return tomllib.loads((wiki_root / WIKI_DIR_NAME / "config.toml").read_text())


def load_config(wiki_root: Path) -> dict[str, Any]:
    """Return the effective config: user-level defaults deep-merged under the wiki's own file."""
    return deep_merge(base=load_user_config(), overlay=load_wiki_config(wiki_root))

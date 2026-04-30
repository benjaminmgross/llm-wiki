"""Locate the wiki root by walking up the filesystem from a start directory.

Mirrors how ``git`` finds its repo root: walk up looking for ``.mdwiki/``;
the first ancestor that contains it is the wiki.
"""

from __future__ import annotations

from pathlib import Path

WIKI_DIR_NAME: str = ".mdwiki"


class WikiNotFound(Exception):
    """Raised when ``find_wiki`` walks all the way to the filesystem root without finding ``.mdwiki/``."""


def find_wiki(start: Path | None = None) -> Path:
    """Walk up from ``start`` looking for a ``.mdwiki/`` directory.

    Parameters
    ----------
    start : Path, optional
        Where to begin the walk. Defaults to the current working directory.

    Returns
    -------
    Path
        The directory containing ``.mdwiki/``.

    Raises
    ------
    WikiNotFound
        If no ancestor (inclusive of ``start``) contains a ``.mdwiki/`` directory.
    """
    here = (start if start is not None else Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        if (candidate / WIKI_DIR_NAME).is_dir():
            return candidate
    raise WikiNotFound(f"No .mdwiki/ found in {here} or any parent — run `mdwiki init` first.")

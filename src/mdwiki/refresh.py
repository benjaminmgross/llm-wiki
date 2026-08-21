"""Re-discover new sources in an already-initialized wiki and register them as pending.

Companion to :func:`mdwiki.init.init_wiki`. Refresh bypasses the early-return
that ``init`` triggers when ``.mdwiki/`` already exists and re-runs the
file-walk + dedup against the live ``state.db``. New files become pending
sources; previously-registered files (matched by content-hash primary key)
are counted in ``dedup_skipped``.

The persisted ``.mdwiki/config.toml`` is the source of truth for loader
configuration; refresh does not accept a ``--profile`` argument because the
profile was baked at init time.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from mdwiki.discover import WIKI_DIR_NAME, find_wiki
from mdwiki.init import InitResult, _register_sources
from mdwiki.loaders import build_registry


def refresh_wiki(target: Path) -> InitResult:
    """Re-scan ``target`` for new sources and register them as pending.

    Parameters
    ----------
    target : Path
        Either the wiki root, or any path inside it. ``find_wiki`` walks up
        looking for ``.mdwiki/``; raises ``WikiNotFound`` if none is reached
        before the filesystem root.

    Returns
    -------
    InitResult
        Same dataclass as ``init_wiki`` returns. ``created`` is always False
        for refresh. ``files_registered`` is the count of newly-pending
        sources from this run; ``dedup_skipped`` covers files whose content
        matched a source registered by any prior init or refresh.

    Raises
    ------
    WikiNotFound
        If ``target`` is not inside an initialized wiki.
    """
    # NOTE: refresh does NOT call _refuse_if_nested (init does). Refresh requires
    # an existing wiki and never creates one, so the nesting invariant was
    # already vetted at init time — re-checking here would be redundant.
    wiki_root = find_wiki(target)
    wiki_dir = wiki_root / WIKI_DIR_NAME
    raw_dir = wiki_root / "raw"

    config = tomllib.loads((wiki_dir / "config.toml").read_text())
    registry = build_registry(config=config, provider=None)

    registered, skipped, dedup_skipped, empty_load_skipped = _register_sources(
        target=wiki_root,
        raw_dir=raw_dir,
        db_path=wiki_dir / "state.db",
        registry=registry,
        exclude_globs=list(config.get("exclude", {}).get("globs", [])),
    )

    suffix_parts: list[str] = []
    if skipped:
        suffix_parts.append(f"skipped {skipped} via ignore rules")
    if dedup_skipped:
        suffix_parts.append(f"skipped {dedup_skipped} already-registered")
    if empty_load_skipped:
        suffix_parts.append(f"skipped {empty_load_skipped} with empty loader output")
    suffix = f" ({'; '.join(suffix_parts)})." if suffix_parts else "."

    return InitResult(
        created=False,
        files_registered=registered,
        files_skipped=skipped,
        dedup_skipped=dedup_skipped,
        empty_load_skipped=empty_load_skipped,
        wiki_root=wiki_root,
        message=(
            f"Refreshed wiki at {wiki_root}/{WIKI_DIR_NAME}/. "
            f"Registered {registered} new source(s)" + suffix
        ),
    )

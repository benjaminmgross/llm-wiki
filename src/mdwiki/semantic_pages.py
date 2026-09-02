"""Shared classification for user-authored semantic wiki pages."""

from __future__ import annotations

from pathlib import PurePosixPath

#: Pages mdwiki owns as navigation or bookkeeping. They are never semantic
#: endpoints for plans, cross-references, lint, or session snapshots.
INFRASTRUCTURE_PAGE_PATHS: frozenset[str] = frozenset(
    {"wiki/index.md", "wiki/log.md", "wiki/concept-table.md", "wiki/overview.md"}
)

#: Deterministically regenerated files (no model involved). Excluded from the
#: page version chain because their bytes are a pure function of state.
#: ``wiki/overview.md`` is model-written and keeps its chain.
GENERATED_PAGE_NAMES: frozenset[str] = frozenset({"index.md", "log.md", "concept-table.md"})


def is_semantic_page_path(path: str | PurePosixPath) -> bool:
    """Return whether a repository-relative path is a semantic Markdown page."""
    normalized = path.as_posix() if isinstance(path, PurePosixPath) else path
    suffix = PurePosixPath(normalized).suffix.lower()
    return normalized.startswith("wiki/") and suffix == ".md" and normalized not in INFRASTRUCTURE_PAGE_PATHS

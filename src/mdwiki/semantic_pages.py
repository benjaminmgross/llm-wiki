"""Shared classification for user-authored semantic wiki pages."""

from __future__ import annotations

from pathlib import PurePosixPath

INFRASTRUCTURE_PAGE_PATHS: frozenset[str] = frozenset({"wiki/index.md", "wiki/log.md"})


def is_semantic_page_path(path: str | PurePosixPath) -> bool:
    """Return whether a repository-relative path is a semantic Markdown page."""
    normalized = path.as_posix() if isinstance(path, PurePosixPath) else path
    suffix = PurePosixPath(normalized).suffix.lower()
    return normalized.startswith("wiki/") and suffix == ".md" and normalized not in INFRASTRUCTURE_PAGE_PATHS

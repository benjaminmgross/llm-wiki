"""Shared helpers for profile-aware wiki page kinds and folders."""

from __future__ import annotations

from pathlib import Path

from mdwiki.plan import allowed_kinds_for_wiki
from mdwiki.semantic_pages import is_semantic_page_path

_SPECIAL_KIND_FOLDERS: dict[str, str] = {
    "entity": "entities",
    "synthesis": "syntheses",
    "status": "status",
    "person": "people",
}
_SPECIAL_FOLDER_KINDS: dict[str, str] = {folder: kind for kind, folder in _SPECIAL_KIND_FOLDERS.items()}


def kind_to_folder(kind: str) -> str:
    """Return the canonical wiki folder for a page kind."""
    return _SPECIAL_KIND_FOLDERS.get(kind, f"{kind}s")


def folder_to_kind(folder: str) -> str:
    """Infer a page kind from a wiki folder name."""
    if folder in _SPECIAL_FOLDER_KINDS:
        return _SPECIAL_FOLDER_KINDS[folder]
    if folder.endswith("ies"):
        return folder[:-3] + "y"
    if folder.endswith("s"):
        return folder[:-1]
    return folder


def heading_for_kind(kind: str) -> str:
    """Return a human heading for a page kind section in ``wiki/index.md``."""
    folder = kind_to_folder(kind)
    return folder.replace("-", " ").title()


def page_kind_folders_for_wiki(wiki_root: Path) -> list[tuple[str, str, str]]:
    """Return ``(kind, heading, folder)`` entries for the active wiki profile."""
    baseline_order = ["entity", "concept", "synthesis"]
    allowed = allowed_kinds_for_wiki(wiki_root)
    ordered = [kind for kind in baseline_order if kind in allowed]
    ordered.extend(sorted(kind for kind in allowed if kind not in set(ordered)))
    return [(kind, heading_for_kind(kind), kind_to_folder(kind)) for kind in ordered]


def iter_wiki_page_paths(wiki_root: Path) -> list[Path]:
    """Return markdown pages under ``wiki/`` excluding generated infrastructure."""
    wiki_dir = wiki_root / "wiki"
    if not wiki_dir.is_dir():
        return []
    paths: list[Path] = []
    for page_path in sorted(wiki_dir.rglob("*.md")):
        rel = page_path.relative_to(wiki_root)
        if not is_semantic_page_path(rel.as_posix()):
            continue
        paths.append(page_path)
    return paths


def infer_kind_from_page_path(page_path: str) -> str | None:
    """Infer kind from ``wiki/<folder>/<page>.md``; return None if unsupported."""
    parts = Path(page_path).parts
    if len(parts) < 3 or parts[0] != "wiki":
        return None
    return folder_to_kind(parts[1])

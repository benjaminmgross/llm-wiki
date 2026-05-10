"""Rebuild the sqlite ``pages`` cache from markdown files under ``wiki/``."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from mdwiki.discover import WIKI_DIR_NAME
from mdwiki.embedder import Embedder, get_default_embedder
from mdwiki.embeddings import serialize
from mdwiki.page_kinds import infer_kind_from_page_path, iter_wiki_page_paths
from mdwiki.plan import allowed_kinds_for_wiki
from mdwiki.state import connect


@dataclass(frozen=True)
class PageIndexRebuildResult:
    """Counts produced by rebuilding ``pages`` from the filesystem."""

    added: int
    updated: int
    pruned: int
    skipped: int
    embedded: int

    @property
    def total_indexed(self) -> int:
        return self.added + self.updated


def rebuild_page_index(
    wiki_root: Path,
    *,
    embedder: Embedder | None = None,
    prune_missing: bool = True,
) -> PageIndexRebuildResult:
    """Rebuild ``pages`` rows and embeddings from existing wiki markdown files."""
    embedder = embedder or get_default_embedder()
    allowed_kinds = allowed_kinds_for_wiki(wiki_root)
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"

    discovered: dict[str, tuple[str, bytes]] = {}
    skipped = 0
    embedded = 0
    for page_path in iter_wiki_page_paths(wiki_root):
        rel = page_path.relative_to(wiki_root).as_posix()
        kind = infer_kind_from_page_path(rel)
        if kind is None or kind not in allowed_kinds:
            skipped += 1
            continue
        content = page_path.read_text()
        discovered[rel] = (kind, serialize(embedder.embed_text(content)))
        embedded += 1

    now = time.time()
    added = 0
    updated = 0
    pruned = 0
    with connect(db_path) as conn:
        existing_paths = {row["path"] for row in conn.execute("SELECT path FROM pages").fetchall()}
        for rel, (kind, embedding) in discovered.items():
            if rel in existing_paths:
                updated += 1
            else:
                added += 1
            conn.execute(
                "INSERT INTO pages (path, kind, embedding, last_touched_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET "
                "kind = excluded.kind, "
                "embedding = excluded.embedding, "
                "last_touched_at = excluded.last_touched_at",
                (rel, kind, embedding, now),
            )

        if prune_missing:
            discovered_paths = set(discovered)
            for path in sorted(existing_paths - discovered_paths):
                conn.execute("DELETE FROM pages WHERE path = ?", (path,))
                pruned += 1
        conn.commit()

    return PageIndexRebuildResult(
        added=added,
        updated=updated,
        pruned=pruned,
        skipped=skipped,
        embedded=embedded,
    )

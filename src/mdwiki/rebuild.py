"""Reconstruct ``.mdwiki/state.db`` from the source-of-truth artifacts.

Source of truth: ``raw/`` (with ``raw/.sources.json`` sidecar) + ``wiki/`` + ``wiki/log.md``.
Embeddings are NOT rebuilt here (they are local and recomputed lazily by ingest/query).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from mdwiki.discover import WIKI_DIR_NAME, find_wiki
from mdwiki.init import SOURCES_SIDECAR_NAME
from mdwiki.state import connect, init_db


class RebuildError(Exception):
    """Raised when the source-of-truth artifacts required for rebuild are missing or unreadable."""


@dataclass(frozen=True)
class RebuildResult:
    """Summary of what was reconstructed."""

    sources_restored: int
    events_replayed: int
    wiki_root: Path
    message: str


def rebuild_wiki(start: Path | None = None) -> RebuildResult:
    """Reconstruct ``state.db`` from ``raw/.sources.json`` and ``wiki/log.md``.

    Parameters
    ----------
    start : Path, optional
        Where to begin the wiki search; defaults to current directory.

    Returns
    -------
    RebuildResult
        Counts and a human-readable summary.

    Raises
    ------
    WikiNotFound
        If no ``.mdwiki/`` is found at or above ``start``.
    RebuildError
        If ``raw/.sources.json`` is missing — without it we cannot recover ``original_path``.
    """
    wiki_root = find_wiki(start)
    sidecar_path = wiki_root / "raw" / SOURCES_SIDECAR_NAME
    if not sidecar_path.is_file():
        raise RebuildError(
            f"Cannot rebuild: missing {sidecar_path}. The sidecar is the source of truth for "
            "original_path metadata; without it state.db cannot be faithfully reconstructed. "
            "Either restore the sidecar from version control or re-run `mdwiki init` against the source folder."
        )

    sidecar = json.loads(sidecar_path.read_text())
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    init_db(db_path)

    with connect(db_path) as conn:
        for short_hash, meta in sidecar.items():
            conn.execute(
                "INSERT INTO sources (id, original_path, raw_path, content_hash, mtime, status) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "original_path = excluded.original_path, "
                "raw_path = excluded.raw_path, "
                "content_hash = excluded.content_hash, "
                "mtime = excluded.mtime",
                (
                    short_hash,
                    meta["original_path"],
                    meta["raw_path"],
                    meta["content_hash"],
                    meta["mtime"],
                    "pending",
                ),
            )
        conn.commit()

    events_replayed = _replay_log(wiki_root=wiki_root, db_path=db_path)

    return RebuildResult(
        sources_restored=len(sidecar),
        events_replayed=events_replayed,
        wiki_root=wiki_root,
        message=f"Rebuilt {wiki_root}/{WIKI_DIR_NAME}/state.db: {len(sidecar)} source(s) restored, {events_replayed} event(s) replayed.",
    )


def _replay_log(*, wiki_root: Path, db_path: Path) -> int:
    """Replay ``wiki/log.md`` into the ``events`` and ``backrefs`` tables.

    Phase 2 deliberately implements this as a no-op when the log is empty or missing;
    the parser arrives in Phase 4 alongside the first event-emitting feature (ingest).
    """
    log_path = wiki_root / "wiki" / "log.md"
    if not log_path.is_file() or not log_path.read_text().strip():
        return 0
    return 0

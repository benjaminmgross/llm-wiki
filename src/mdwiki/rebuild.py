"""Reconstruct ``.mdwiki/state.db`` from the source-of-truth artifacts.

Source of truth: ``raw/`` (with ``raw/.sources.json`` sidecar) + ``wiki/`` + ``wiki/log.md``.
Embeddings are NOT rebuilt here (they are local and recomputed lazily by ingest/query).

**v1.0.0 limitation.** Only the ``sources`` table is restored from the sidecar.
``backrefs``, ``pages``, and ``events`` are NOT replayable from ``log.md`` alone
— each line is just ``- <ts> [<tx_id>] <summary>``, which is too lossy to
reconstruct row-level facts. To recover those tables, re-ingest the relevant
sources after rebuild. Older sidecars without status metadata restore sources
as ``pending`` so ``mdwiki ingest --pending`` will sweep them up. This is
documented in the spec at ``docs/mdwiki-design.md`` v1.0.5.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
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
    """Reconstruct the ``sources`` table in ``state.db`` from ``raw/.sources.json``.

    **v1.0.0 scope.** Only ``sources`` is restored. ``backrefs`` / ``pages`` /
    ``events`` cannot be reconstructed from ``wiki/log.md`` alone because the
    log format (``- <ts> [<tx_id>] <summary>``) is too lossy. Older sidecars
    without source status metadata restore rows as ``pending``.

    Parameters
    ----------
    start : Path, optional
        Where to begin the wiki search; defaults to current directory.

    Returns
    -------
    RebuildResult
        Counts and a human-readable summary. ``events_replayed`` is always 0
        in v1.0.0 (kept in the result shape for forward compatibility).

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
    inferred_ingested = _infer_ingested_sources_from_log(wiki_root)
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    init_db(db_path)

    with connect(db_path) as conn:
        for short_hash, meta in sidecar.items():
            inferred_ingested_at = inferred_ingested.get(meta["original_path"])
            status = meta.get("status") or ("ingested" if inferred_ingested_at is not None else "pending")
            if status not in {"pending", "ingested", "failed"}:
                status = "pending"
            ingested_at = meta.get("ingested_at", inferred_ingested_at) if status == "ingested" else None
            conn.execute(
                "INSERT INTO sources (id, original_path, raw_path, content_hash, mtime, status, ingested_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "original_path = excluded.original_path, "
                "raw_path = excluded.raw_path, "
                "content_hash = excluded.content_hash, "
                "mtime = excluded.mtime, "
                "status = excluded.status, "
                "ingested_at = excluded.ingested_at",
                (
                    short_hash,
                    meta["original_path"],
                    meta["raw_path"],
                    meta["content_hash"],
                    meta["mtime"],
                    status,
                    ingested_at,
                ),
            )
        conn.commit()

    return RebuildResult(
        sources_restored=len(sidecar),
        events_replayed=0,
        wiki_root=wiki_root,
        message=(
            f"Rebuilt {wiki_root}/{WIKI_DIR_NAME}/state.db: {len(sidecar)} source(s) restored "
            "from raw/.sources.json. backrefs/events/pages are NOT replayable from log.md alone "
            "— re-ingest sources if those cache tables must be recovered."
        ),
    )


def _infer_ingested_sources_from_log(wiki_root: Path) -> dict[str, float | None]:
    """Infer legacy source statuses from append-only log lines.

    Older sidecars did not persist ``sources.status``. ``wiki/log.md`` cannot
    restore page/backref/event rows, but ingest summary lines do preserve the
    original source path well enough to avoid re-marking every source pending.
    """
    log_path = wiki_root / "wiki" / "log.md"
    if not log_path.is_file():
        return {}

    ingested_by_tx: dict[str, tuple[str, float | None]] = {}
    undone_tx_ids: set[str] = set()
    for line in log_path.read_text().splitlines():
        tx_id = _extract_tx_id(line)
        if tx_id is None:
            continue
        if "[undo]" in line and " reverted " in line:
            undone_tx_ids.add(line.rsplit(" reverted ", 1)[1].strip())
            continue
        source_path = _extract_ingested_source_path(line)
        if source_path is not None:
            ingested_by_tx[tx_id] = (source_path, _extract_log_ts(line))

    return {
        source_path: ingested_at
        for tx_id, (source_path, ingested_at) in ingested_by_tx.items()
        if tx_id not in undone_tx_ids
    }


def _extract_tx_id(line: str) -> str | None:
    try:
        return line.split("[", 1)[1].split("]", 1)[0]
    except IndexError:
        return None


def _extract_ingested_source_path(line: str) -> str | None:
    marker = "] ingest "
    if marker not in line or " → " not in line:
        return None
    return line.split(marker, 1)[1].split(" → ", 1)[0]


def _extract_log_ts(line: str) -> float | None:
    if not line.startswith("- "):
        return None
    raw = line[2:].split(" ", 1)[0]
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC).timestamp()
    except ValueError:
        return None

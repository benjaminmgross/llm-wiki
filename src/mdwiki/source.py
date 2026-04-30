"""Inspect a single registered source by hash or hash prefix."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mdwiki.discover import WIKI_DIR_NAME
from mdwiki.state import connect


@dataclass(frozen=True)
class SourceInfo:
    """All metadata for a single registered source."""

    short_hash: str
    original_path: str
    raw_path: str
    content_hash: str
    status: str
    ingested_at: float | None
    dependent_pages: tuple[str, ...]


def find_matching_sources(wiki_root: Path, hash_prefix: str) -> list[str]:
    """Return every source ``id`` whose value starts with ``hash_prefix``.

    Parameters
    ----------
    wiki_root : Path
        Folder containing ``.mdwiki/``.
    hash_prefix : str
        First N hex characters of the source's content hash; can be the full id.

    Returns
    -------
    list[str]
        Matching ids; empty if no source begins with the prefix.
    """
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id FROM sources WHERE id LIKE ? ORDER BY id",
            (hash_prefix + "%",),
        ).fetchall()
    return [row["id"] for row in rows]


def get_source_info(wiki_root: Path, short_hash: str) -> SourceInfo:
    """Return full metadata for ``short_hash``.

    Parameters
    ----------
    wiki_root : Path
        Folder containing ``.mdwiki/``.
    short_hash : str
        The exact 12-character source id.

    Raises
    ------
    LookupError
        If no source with this id is registered.
    """
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, original_path, raw_path, content_hash, status, ingested_at FROM sources WHERE id = ?",
            (short_hash,),
        ).fetchone()
        if row is None:
            raise LookupError(f"No source with id {short_hash!r}")
        page_rows = conn.execute(
            "SELECT DISTINCT page_path FROM backrefs WHERE source_id = ? ORDER BY page_path",
            (short_hash,),
        ).fetchall()

    return SourceInfo(
        short_hash=row["id"],
        original_path=row["original_path"],
        raw_path=row["raw_path"],
        content_hash=row["content_hash"],
        status=row["status"],
        ingested_at=row["ingested_at"],
        dependent_pages=tuple(page["page_path"] for page in page_rows),
    )


def format_source_info(info: SourceInfo) -> str:
    """Render ``SourceInfo`` as a multi-line block for the CLI."""
    ingested = "never" if info.ingested_at is None else _fmt_ts(info.ingested_at)
    lines = [
        info.short_hash,
        f"  original_path:   {info.original_path}",
        f"  raw_path:        {info.raw_path}",
        f"  content_hash:    {info.content_hash}",
        f"  status:          {info.status}",
        f"  ingested_at:     {ingested}",
    ]
    if info.dependent_pages:
        lines.append(f"  dependent pages ({len(info.dependent_pages)}):")
        for page in info.dependent_pages:
            lines.append(f"    - {page}")
    else:
        lines.append("  dependent pages: (none)")
    return "\n".join(lines)


def format_disambiguation(matches: list[str]) -> str:
    """Render the multi-match list for ambiguous prefixes."""
    lines = [f"Ambiguous prefix matches {len(matches)} sources:"]
    for m in matches:
        lines.append(f"  {m}")
    lines.append("Re-run with a longer prefix.")
    return "\n".join(lines)


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d %H:%M:%SZ")

"""Regenerate ``wiki/log.md`` from the ``events`` table — recovery utility.

The events table is the source of truth; ``log.md`` is the flat representation that
``mdwiki status`` and humans browse. Running ingest pipelines write to both, but the
DB commit happens FIRST so a crash mid-write can leave ``log.md`` lagging the events
table by one or more lines. ``rebuild_log`` regenerates the file from scratch — every
event with a ``transaction_id`` becomes one log line, ordered chronologically.

Lint events (which have NULL ``transaction_id``) are deliberately skipped: ``log.md``
has only ever recorded transactional changes, and lint has its own surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mdwiki.discover import WIKI_DIR_NAME
from mdwiki.state import connect


@dataclass(frozen=True)
class RebuildLogResult:
    """Outcome of a ``rebuild_log`` call.

    Parameters
    ----------
    lines_written : int
        Count of transactional events emitted to ``wiki/log.md``.
    log_path : Path
        Absolute path of the regenerated file.
    """

    lines_written: int
    log_path: Path


def rebuild_log(wiki_root: Path) -> RebuildLogResult:
    """Overwrite ``wiki/log.md`` with one line per transactional event in chronological order.

    Parameters
    ----------
    wiki_root : Path
        Folder containing ``.mdwiki/`` (where the state.db lives).
    """
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT ts, transaction_id, summary FROM events "
            "WHERE transaction_id IS NOT NULL "
            "ORDER BY ts ASC, id ASC"
        ).fetchall()

    wiki_dir = wiki_root / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    log_path = wiki_dir / "log.md"

    lines = [f"- {_iso_utc(row['ts'])} [{row['transaction_id']}] {row['summary']}\n" for row in rows]
    log_path.write_text("".join(lines))
    return RebuildLogResult(lines_written=len(lines), log_path=log_path)


def _iso_utc(ts: float) -> str:
    """Format a unix timestamp as ``YYYY-MM-DDTHH:MM:SSZ`` — matches transaction.py:278."""
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

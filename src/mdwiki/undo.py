"""Atomic rollback of the most recent N applied transactions.

Reverses both file writes (from per-tx snapshot directories) and DB rows
(via the ``transaction_inverses`` rows recorded at commit time). Each tx's
inverses run in LIFO order so dependent rows come out before their parents.
The whole undo runs inside a single sqlite transaction; if anything fails
mid-rollback, nothing changes.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from mdwiki.discover import WIKI_DIR_NAME
from mdwiki.search import rebuild_search_index
from mdwiki.state import connect
from mdwiki.transaction import DELETION_MARKER_DIR


class UndoError(Exception):
    """Raised when undo cannot proceed (no wiki, bad N, etc.)."""


@dataclass(frozen=True)
class UndoResult:
    """Outcome of one ``undo_last`` call."""

    undone_count: int
    undone_tx_ids: tuple[str, ...]
    message: str


def undo_last(wiki_root: Path, n: int = 1) -> UndoResult:
    """Undo the most recent ``n`` applied transactions.

    Parameters
    ----------
    wiki_root : Path
        Folder containing ``.mdwiki/``.
    n : int, optional
        How many transactions to undo (default 1). Must be ≥ 1.

    Raises
    ------
    UndoError
        If ``n < 1`` or if no wiki is reachable.
    """
    if n < 1:
        raise UndoError(f"n must be ≥ 1, got {n}")
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    if not db_path.is_file():
        raise UndoError(f"No wiki at {wiki_root}/{WIKI_DIR_NAME}/. Run `mdwiki init` first.")

    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, ts, undo_snapshot_path FROM transactions WHERE applied = 1 ORDER BY ts DESC LIMIT ?",
            (n,),
        ).fetchall()
        if not rows:
            return UndoResult(undone_count=0, undone_tx_ids=(), message="No applied transactions to undo.")

        undone_ids: list[str] = []
        try:
            for row in rows:
                _undo_one_transaction(conn=conn, wiki_root=wiki_root, tx_row=row)
                undone_ids.append(row["id"])
            # Page files were restored from snapshots above; the lexical index
            # is a derived cache, so rebuild it from disk in the same commit.
            rebuild_search_index(conn, wiki_root=wiki_root)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    _sync_source_sidecar_from_db(wiki_root=wiki_root)
    _append_undo_markers(wiki_root=wiki_root, tx_ids=undone_ids)

    return UndoResult(
        undone_count=len(undone_ids),
        undone_tx_ids=tuple(undone_ids),
        message=f"Undid {len(undone_ids)} transaction(s): {', '.join(undone_ids)}",
    )


def _undo_one_transaction(*, conn, wiki_root: Path, tx_row) -> None:
    """Roll back a single transaction's DB rows and file writes."""
    tx_id = tx_row["id"]

    inverse_rows = conn.execute(
        "SELECT sql, params_json FROM transaction_inverses WHERE transaction_id = ? ORDER BY id DESC",
        (tx_id,),
    ).fetchall()
    for inv in inverse_rows:
        params = tuple(json.loads(inv["params_json"]))
        conn.execute(inv["sql"], params)

    conn.execute("DELETE FROM events WHERE transaction_id = ?", (tx_id,))
    conn.execute("DELETE FROM transaction_inverses WHERE transaction_id = ?", (tx_id,))
    conn.execute("UPDATE transactions SET applied = 0 WHERE id = ?", (tx_id,))

    snapshot_dir = wiki_root / tx_row["undo_snapshot_path"]
    _restore_files_from_snapshot(snapshot_dir=snapshot_dir, wiki_root=wiki_root)


def _restore_files_from_snapshot(*, snapshot_dir: Path, wiki_root: Path) -> None:
    """Replay the per-tx snapshot: existing-file copies are restored; deletion markers delete."""
    if not snapshot_dir.is_dir():
        return

    deletion_root = snapshot_dir / DELETION_MARKER_DIR
    if deletion_root.is_dir():
        for marker in deletion_root.rglob("*"):
            if marker.is_file():
                rel = marker.relative_to(deletion_root)
                target = wiki_root / rel
                if target.is_file():
                    target.unlink()

    for snap_file in snapshot_dir.rglob("*"):
        if not snap_file.is_file():
            continue
        try:
            rel = snap_file.relative_to(snapshot_dir)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] == DELETION_MARKER_DIR:
            continue
        target = wiki_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snap_file, target)

    shutil.rmtree(snapshot_dir, ignore_errors=True)


def _append_undo_markers(*, wiki_root: Path, tx_ids: list[str]) -> None:
    """Karpathy log is append-only; undos record themselves rather than deleting prior lines."""
    if not tx_ids:
        return
    log_path = wiki_root / "wiki" / "log.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    now_iso = datetime.fromtimestamp(time.time(), tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with log_path.open("a") as fh:
        for tx_id in tx_ids:
            fh.write(f"- {now_iso} [undo] reverted {tx_id}\n")


def _sync_source_sidecar_from_db(*, wiki_root: Path) -> None:
    """Keep tracked source recovery metadata consistent after undo."""
    sidecar_path = wiki_root / "raw" / ".sources.json"
    if not sidecar_path.is_file():
        return

    sidecar = json.loads(sidecar_path.read_text())
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    with connect(db_path) as conn:
        rows = conn.execute("SELECT id, status, ingested_at, failure_reason FROM sources").fetchall()

    changed = False
    for row in rows:
        meta = sidecar.get(row["id"])
        if not isinstance(meta, dict):
            continue
        if (
            meta.get("status") != row["status"]
            or meta.get("ingested_at") != row["ingested_at"]
            or meta.get("failure_reason") != row["failure_reason"]
        ):
            meta["status"] = row["status"]
            meta["ingested_at"] = row["ingested_at"]
            meta["failure_reason"] = row["failure_reason"]
            changed = True

    if not changed:
        return

    tmp_path = sidecar_path.with_name(f"{sidecar_path.name}.tmp-undo")
    try:
        tmp_path.write_text(json.dumps(sidecar, indent=2, sort_keys=True))
        import os

        os.replace(tmp_path, sidecar_path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

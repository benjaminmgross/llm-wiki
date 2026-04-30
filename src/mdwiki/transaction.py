"""Atomic ingest transaction with per-tx undo snapshot.

Every wiki write goes through this context manager. It guarantees three things:

1. **Atomicity** — either every write commits together or none of them stick.
2. **Undo** — pre-modification copies of touched files land under
   ``.mdwiki/undo/<tx_id>/``, with a sibling ``__delete_on_undo__/`` directory
   marking files that were created (so undo deletes them rather than restores).
3. **Audit** — a row in ``transactions``, an ``events`` row, and a line in
   ``wiki/log.md`` arrive together with the file changes.

If the ``with`` block raises (or calls ``abort()``), the snapshot is replayed:
modified files are restored, created files are removed, and the database tx is
rolled back. ``state.db`` ends up in the same state as before the ``with``.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
import uuid
from pathlib import Path
from types import TracebackType
from typing import Self

from mdwiki.discover import WIKI_DIR_NAME
from mdwiki.state import connect

UNDO_DIR_NAME: str = "undo"
DELETION_MARKER_DIR: str = "__delete_on_undo__"


class TransactionAborted(RuntimeError):
    """Raised by ``IngestTransaction.abort()`` to trigger rollback."""


class IngestTransaction:
    """Context manager wrapping a single atomic wiki edit."""

    def __init__(self, *, wiki_root: Path, source_id: str | None, summary: str) -> None:
        """Initialize the transaction.

        Parameters
        ----------
        wiki_root : Path
            Folder containing ``.mdwiki/``.
        source_id : str or None
            The 12-char source id this transaction ingests, if any. ``None`` for
            transactions that aren't tied to a single source (e.g. lint --fix).
        summary : str
            Human-readable one-line summary, written to both ``events.summary`` and ``log.md``.
        """
        self._wiki_root = wiki_root
        self._source_id = source_id
        self._summary = summary
        self._tx_id: str = f"tx-{uuid.uuid4().hex[:12]}"
        self._undo_dir = wiki_root / WIKI_DIR_NAME / UNDO_DIR_NAME / self._tx_id
        self._touched_files: list[Path] = []
        self._conn: sqlite3.Connection | None = None
        self._committed: bool = False
        self._inverses: list[tuple[str, tuple]] = []
        self._prev_source_state: tuple[str, float | None] | None = None

    @property
    def tx_id(self) -> str:
        return self._tx_id

    def __enter__(self) -> Self:
        self._undo_dir.mkdir(parents=True, exist_ok=True)
        self._conn = connect(self._wiki_root / WIKI_DIR_NAME / "state.db")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool:
        try:
            if exc_type is None:
                self._commit()
            else:
                self._rollback()
        finally:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
        return False

    def write_file(self, path: Path, content: str) -> None:
        """Write ``content`` to ``path``. Snapshots the previous file (or marks for deletion if new)."""
        path = path.resolve()
        rel = path.relative_to(self._wiki_root.resolve())

        if path.exists():
            snapshot_target = self._undo_dir / rel
            snapshot_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, snapshot_target)
        else:
            marker = self._undo_dir / DELETION_MARKER_DIR / rel
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("")

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        self._touched_files.append(path)

    def add_inverse(self, sql: str, params: tuple) -> None:
        """Record an inverse SQL statement to run on undo.

        Inverses are appended in apply order; ``mdwiki undo`` runs them in reverse
        (LIFO) so dependent rows are removed before the rows they depend on.

        Parameters
        ----------
        sql : str
            A parameterized SQL statement that, when executed, reverses one
            specific change made during this transaction.
        params : tuple
            Bound parameters for the statement. Stored as JSON inside the
            ``transaction_inverses`` table.
        """
        self._inverses.append((sql, params))

    def upsert_page(self, *, path: str, kind: str, embedding: bytes | None, last_touched_at: float) -> None:
        """Insert or update one ``pages`` row, recording the inverse for undo."""
        assert self._conn is not None
        prev = self._conn.execute(
            "SELECT kind, embedding, last_touched_at FROM pages WHERE path = ?", (path,)
        ).fetchone()
        if prev is None:
            self._conn.execute(
                "INSERT INTO pages (path, kind, embedding, last_touched_at) VALUES (?, ?, ?, ?)",
                (path, kind, embedding, last_touched_at),
            )
            self.add_inverse("DELETE FROM pages WHERE path = ?", (path,))
        else:
            self._conn.execute(
                "UPDATE pages SET kind = ?, embedding = ?, last_touched_at = ? WHERE path = ?",
                (kind, embedding, last_touched_at, path),
            )
            self.add_inverse(
                "UPDATE pages SET kind = ?, embedding = ?, last_touched_at = ? WHERE path = ?",
                (prev["kind"], prev["embedding"], prev["last_touched_at"], path),
            )

    def insert_backref(self, *, page_path: str, source_id: str, section_anchor: str | None, quote: str) -> None:
        """Insert a ``backrefs`` row, recording the deletion-by-rowid inverse."""
        assert self._conn is not None
        cursor = self._conn.execute(
            "INSERT INTO backrefs (page_path, source_id, section_anchor, quote) VALUES (?, ?, ?, ?)",
            (page_path, source_id, section_anchor, quote),
        )
        self.add_inverse("DELETE FROM backrefs WHERE rowid = ?", (cursor.lastrowid,))

    def abort(self, reason: str = "aborted") -> None:
        """Trigger rollback by raising ``TransactionAborted``."""
        raise TransactionAborted(reason)

    def _commit(self) -> None:
        assert self._conn is not None
        now = time.time()

        self._conn.execute(
            "INSERT INTO transactions (id, ts, undo_snapshot_path, applied) VALUES (?, ?, ?, ?)",
            (self._tx_id, now, str(self._undo_dir.relative_to(self._wiki_root)), 1),
        )
        self._conn.execute(
            "INSERT INTO events (ts, kind, source_id, summary, transaction_id) VALUES (?, ?, ?, ?, ?)",
            (now, "ingest", self._source_id, self._summary, self._tx_id),
        )
        if self._source_id is not None:
            prev = self._conn.execute(
                "SELECT status, ingested_at FROM sources WHERE id = ?", (self._source_id,)
            ).fetchone()
            self._prev_source_state = (prev["status"], prev["ingested_at"]) if prev is not None else None
            self._conn.execute(
                "UPDATE sources SET status = 'ingested', ingested_at = ? WHERE id = ?",
                (now, self._source_id),
            )
            if self._prev_source_state is not None:
                prev_status, prev_ingested_at = self._prev_source_state
                self.add_inverse(
                    "UPDATE sources SET status = ?, ingested_at = ? WHERE id = ?",
                    (prev_status, prev_ingested_at, self._source_id),
                )

        for sql, params in self._inverses:
            self._conn.execute(
                "INSERT INTO transaction_inverses (transaction_id, sql, params_json) VALUES (?, ?, ?)",
                (self._tx_id, sql, json.dumps(list(params))),
            )

        log_path = self._wiki_root / "wiki" / "log.md"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a") as fh:
            fh.write(f"- {_iso_utc(now)} [{self._tx_id}] {self._summary}\n")

        self._conn.commit()
        self._committed = True

    def _rollback(self) -> None:
        for touched in self._touched_files:
            rel = touched.relative_to(self._wiki_root.resolve())
            snapshot = self._undo_dir / rel
            if snapshot.exists():
                shutil.copy2(snapshot, touched)
            else:
                if touched.exists():
                    touched.unlink()
        if self._conn is not None:
            self._conn.rollback()
        if self._undo_dir.exists():
            shutil.rmtree(self._undo_dir)


def _iso_utc(ts: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

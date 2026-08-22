"""Atomic ingest transaction with per-tx undo snapshot.

Every wiki write goes through this context manager. It guarantees three things:

1. **Atomicity** — either every write commits together or none of them stick.
   File writes use the standard ``write-temp + os.replace`` pattern so a
   crash mid-write leaves the original file intact (atomic on POSIX +
   Windows).
2. **Undo** — pre-modification copies of touched files land under
   ``.mdwiki/undo/<tx_id>/``, with a sibling ``__delete_on_undo__/`` directory
   marking files that were created (so undo deletes them rather than restores).
3. **Audit** — a row in ``transactions``, an ``events`` row, and a line in
   ``wiki/log.md`` arrive together with the file changes. The DB is the
   source of truth: ``log.md`` is appended AFTER ``conn.commit()``; if the
   log append fails post-commit, the DB row stays intact and a stderr
   warning is printed (the events table can be re-rendered to log.md).

If the ``with`` block raises (or calls ``abort()``), the snapshot is replayed:
modified files are restored, created files are removed, and the database tx is
rolled back. ``state.db`` ends up in the same state as before the ``with``.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from types import TracebackType
from typing import Literal, Self

from mdwiki.discover import WIKI_DIR_NAME
from mdwiki.source_state import mirror_source_state
from mdwiki.state import connect

UNDO_DIR_NAME: str = "undo"
DELETION_MARKER_DIR: str = "__delete_on_undo__"
WRITE_LOCK_NAME: str = "write.lock"


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
        self._write_lock_conn: sqlite3.Connection | None = None
        self._committed: bool = False
        self._inverses: list[tuple[str, tuple[object, ...]]] = []
        self._prev_source_state: tuple[str, float | None, str | None] | None = None

    @property
    def tx_id(self) -> str:
        return self._tx_id

    def __enter__(self) -> Self:
        self._write_lock_conn = _acquire_write_lock(self._wiki_root)
        try:
            self._undo_dir.mkdir(parents=True, exist_ok=True)
            self._conn = connect(self._wiki_root / WIKI_DIR_NAME / "state.db")
            self._conn.execute("BEGIN IMMEDIATE")
        except Exception:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            _release_write_lock(self._write_lock_conn)
            self._write_lock_conn = None
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> Literal[False]:
        try:
            if exc_type is None:
                self._commit()
            else:
                self._rollback()
        finally:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            if self._write_lock_conn is not None:
                _release_write_lock(self._write_lock_conn)
                self._write_lock_conn = None
        return False

    def write_file(self, path: Path, content: str) -> str:
        """Atomically write ``content`` to ``path``.

        Writes to ``<path>.tmp-<tx_id>`` first, then ``os.replace`` swaps it
        into place. Atomic on POSIX (rename within a filesystem) and Windows
        (``os.replace`` semantics). A SIGKILL/power-loss between the temp
        write and the rename leaves the original file untouched.

        Snapshots the previous file (or marks the path for deletion if new)
        so rollback can undo the change.

        For paths under ``wiki/`` (i.e. wiki page writes), the prior body's
        SHA-256 hash is automatically embedded into the new content's YAML
        frontmatter as ``previous_hash:``, building a tamper-evident page
        version chain (see ``version_chain.py``). Non-wiki writes pass through
        unchanged.
        """
        path = path.resolve()
        rel = path.relative_to(self._wiki_root.resolve())

        prev_body: str | None = None
        if path.exists():
            snapshot_target = self._undo_dir / rel
            snapshot_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, snapshot_target)
            # Snapshot already on disk — read for the version chain BEFORE
            # the os.replace below clobbers the file.
            if _is_wiki_page(rel):
                try:
                    with path.open(encoding="utf-8", newline="") as previous_file:
                        prev_body = previous_file.read()
                except OSError:
                    prev_body = None
        else:
            marker = self._undo_dir / DELETION_MARKER_DIR / rel
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("")

        # Embed previous_hash for wiki/ pages. New pages get a "genesis"
        # marker (no previous_hash key) — a missing key is itself meaningful
        # ("this is the first version").
        if _is_wiki_page(rel):
            from mdwiki.version_chain import prepare_wiki_page_content

            content = prepare_wiki_page_content(content=content, previous_body=prev_body)

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f"{path.name}.tmp-{self._tx_id}")
        try:
            with tmp_path.open("w", encoding="utf-8", newline="") as temp_file:
                _ = temp_file.write(content)
            import os

            os.replace(tmp_path, path)
        except Exception:
            # Best-effort cleanup of the partial temp file before re-raising.
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise
        self._touched_files.append(path)
        return content

    def add_inverse(self, sql: str, params: tuple[object, ...]) -> None:
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
        """Insert or update one ``pages`` row, recording the inverse for undo.

        On UPDATE, the inverse intentionally NULLs the ``embedding`` column
        rather than restoring the previous bytes. Two reasons:

        1. ``transaction_inverses.params_json`` is JSON-serialized
           (``json.dumps`` on the bound params) and ``json.dumps`` cannot
           encode ``bytes``. Round-tripping the prior embedding through JSON
           would require base64 hops on both write and undo paths.
        2. Page embeddings are deterministic from page content, and on undo
           the file content is restored from the on-disk snapshot anyway.
           A subsequent reader (ANN search, query, next ingest) will simply
           re-derive the embedding when needed — the prior bytes are a cache,
           not state worth preserving.
        """
        if self._conn is None:
            raise RuntimeError("IngestTransaction must be entered as a context manager before calling upsert_page().")
        prev = self._conn.execute("SELECT kind, embedding, last_touched_at FROM pages WHERE path = ?", (path,)).fetchone()
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
            # Drop the prior embedding bytes from the inverse — see docstring.
            self.add_inverse(
                "UPDATE pages SET kind = ?, embedding = NULL, last_touched_at = ? WHERE path = ?",
                (prev["kind"], prev["last_touched_at"], path),
            )

    def insert_backref(self, *, page_path: str, source_id: str, section_anchor: str | None, quote: str) -> None:
        """Insert a ``backrefs`` row, recording the deletion-by-rowid inverse."""
        if self._conn is None:
            raise RuntimeError("IngestTransaction must be entered as a context manager before calling insert_backref().")
        cursor = self._conn.execute(
            "INSERT INTO backrefs (page_path, source_id, section_anchor, quote) VALUES (?, ?, ?, ?)",
            (page_path, source_id, section_anchor, quote),
        )
        self.add_inverse("DELETE FROM backrefs WHERE rowid = ?", (cursor.lastrowid,))

    def abort(self, reason: str = "aborted") -> None:
        """Trigger rollback by raising ``TransactionAborted``."""
        raise TransactionAborted(reason)

    def _commit(self) -> None:
        if self._conn is None:
            raise RuntimeError("IngestTransaction must be entered as a context manager before calling _commit().")
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
            prev = self._conn.execute("SELECT status, ingested_at, failure_reason FROM sources WHERE id = ?", (self._source_id,)).fetchone()
            self._prev_source_state = (prev["status"], prev["ingested_at"], prev["failure_reason"]) if prev is not None else None
            self._conn.execute(
                "UPDATE sources SET status = 'ingested', ingested_at = ?, failure_reason = NULL WHERE id = ?",
                (now, self._source_id),
            )
            if self._prev_source_state is not None:
                prev_status, prev_ingested_at, prev_failure_reason = self._prev_source_state
                self.add_inverse(
                    "UPDATE sources SET status = ?, ingested_at = ?, failure_reason = ? WHERE id = ?",
                    (prev_status, prev_ingested_at, prev_failure_reason, self._source_id),
                )

        for sql, params in self._inverses:
            self._conn.execute(
                "INSERT INTO transaction_inverses (transaction_id, sql, params_json) VALUES (?, ?, ?)",
                (self._tx_id, sql, json.dumps(list(params))),
            )

        # The DB is the source of truth — commit it BEFORE touching log.md so a
        # disk-full or permission failure on the log append can't leave a
        # half-line in the file with no DB row to match. If the post-commit
        # log write fails we warn loudly but leave the DB intact (the events
        # table can be replayed to log.md later).
        self._conn.commit()
        self._committed = True
        if self._source_id is not None:
            try:
                mirror_source_state(
                    self._wiki_root,
                    source_id=self._source_id,
                    status="ingested",
                    ingested_at=now,
                    failure_reason=None,
                )
            except Exception as exc:
                print(
                    f"warning: source status committed to DB but raw/.sources.json update failed: {exc} "
                    f"(source_id={self._source_id}; rebuild may mark this source pending)",
                    file=sys.stderr,
                )

        log_path = self._wiki_root / "wiki" / "log.md"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a") as fh:
                fh.write(f"- {_iso_utc(now)} [{self._tx_id}] {self._summary}\n")
        except OSError as exc:
            print(
                f"warning: event committed to DB but log.md append failed: {exc} "
                f"(tx_id={self._tx_id}; events table is the source of truth)",
                file=sys.stderr,
            )

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


def _is_wiki_page(rel: Path) -> bool:
    """Return True when ``rel`` (relative to wiki_root) names a wiki page eligible for version-chain embedding.

    Pages live under ``wiki/`` and are markdown. We deliberately exclude the
    auto-generated ``wiki/log.md`` and ``wiki/index.md`` because their format
    is line-oriented / catalog-style respectively, and embedding YAML
    frontmatter would corrupt them.
    """
    if not rel.parts or rel.parts[0] != "wiki":
        return False
    if rel.suffix.lower() != ".md":
        return False
    name = rel.name.lower()
    if name in {"log.md", "index.md"}:
        return False
    return True


def _acquire_write_lock(wiki_root: Path) -> sqlite3.Connection:
    """Reserve the wiki-wide write boundary for files, state, sidecar, and log."""
    lock_path = wiki_root / WIKI_DIR_NAME / WRITE_LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(lock_path, timeout=30.0)
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        conn.execute("BEGIN IMMEDIATE")
    except Exception:
        conn.close()
        raise
    return conn


def _release_write_lock(conn: sqlite3.Connection) -> None:
    """Release a lock acquired by :func:`_acquire_write_lock`."""
    try:
        conn.rollback()
    finally:
        conn.close()

"""Durable source failure state shared by sync and native-batch ingest."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from mdwiki.discover import WIKI_DIR_NAME
from mdwiki.state import connect


def mark_source_failed(wiki_root: Path, source_id: str, reason: str) -> None:
    """Mark one source retryable-failed and mirror the state into the raw sidecar."""
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE sources SET status = 'failed', ingested_at = NULL, failure_reason = ? WHERE id = ?",
            (reason, source_id),
        )
        conn.commit()
    try:
        mirror_source_state(
            wiki_root,
            source_id=source_id,
            status="failed",
            ingested_at=None,
            failure_reason=reason,
        )
    except Exception as exc:
        print(
            f"warning: source failure committed to DB but raw/.sources.json update failed: {exc} "
            f"(source_id={source_id}; rebuild may lose the failure reason)",
            file=sys.stderr,
        )


def mirror_source_state(
    wiki_root: Path,
    *,
    source_id: str,
    status: str,
    ingested_at: float | None,
    failure_reason: str | None,
) -> None:
    """Atomically mirror source recovery metadata into ``raw/.sources.json``."""
    sidecar_path = wiki_root / "raw" / ".sources.json"
    if not sidecar_path.is_file():
        return
    sidecar = json.loads(sidecar_path.read_text())
    meta = sidecar.get(source_id)
    if not isinstance(meta, dict):
        return
    meta["status"] = status
    meta["ingested_at"] = ingested_at
    meta["failure_reason"] = failure_reason
    tmp_path = sidecar_path.with_name(f"{sidecar_path.name}.tmp-{source_id}")
    try:
        tmp_path.write_text(json.dumps(sidecar, indent=2, sort_keys=True))
        os.replace(tmp_path, sidecar_path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass

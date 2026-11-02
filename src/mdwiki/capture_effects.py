"""Deterministic, undo-aware projections of captured conclusions.

Raw captures and their sidecar metadata are durable. This module owns the
projection into page frontmatter, supersession callouts, and standing rules.
All apply operations run under the ingest writer's existing transaction.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import quote

import yaml

from mdwiki.capture import CaptureError, CaptureRecord, get_capture, load_captures
from mdwiki.discover import WIKI_DIR_NAME
from mdwiki.init import _write_registration_sidecar
from mdwiki.semantic_pages import is_semantic_page_path
from mdwiki.state import connect
from mdwiki.transaction import IngestTransaction, _acquire_write_lock, _release_write_lock
from mdwiki.version_chain import strip_frontmatter

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


def page_metadata(*, content: str) -> dict[str, object]:
    """Read page frontmatter without mistaking the body for metadata."""
    match = _FRONTMATTER.match(content)
    if match is None:
        return {}
    try:
        parsed: object = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise CaptureError(f"Invalid page frontmatter; repair the YAML before applying captures: {exc}") from exc
    if not isinstance(parsed, dict) or any(not isinstance(key, str) for key in parsed):
        return {}
    return cast(dict[str, object], parsed)


def _metadata_ids(*, metadata: dict[str, object], key: str) -> list[str]:
    values = metadata.get(key, [])
    return [value for value in values if isinstance(value, str)] if isinstance(values, list) else []


def _with_metadata(*, content: str, updates: dict[str, object]) -> str:
    metadata = page_metadata(content=content)
    metadata.update(updates)
    return "---\n" + yaml.safe_dump(metadata, sort_keys=False).rstrip() + "\n---\n" + strip_frontmatter(content)


def preserve_capture_metadata(*, previous: str, content: str) -> str:
    """Retain managed ownership/history when a model replaces a complete page."""
    if not any(key in previous for key in ("capture_id:", "capture_ids:", "superseded_by:")):
        return content
    metadata = page_metadata(content=previous)
    managed = {
        key: value
        for key, value in metadata.items()
        if key in {"capture_id", "capture_ids", "pinned_capture_ids", "pinned", "superseded_by"}
    }
    return _with_metadata(content=content, updates=managed) if managed else content


def _records(*, wiki_root: Path, conn: sqlite3.Connection, current_source_id: str | None = None) -> list[CaptureRecord]:
    if conn.execute("SELECT 1 FROM sources WHERE original_path LIKE 'capture://%' LIMIT 1").fetchone() is None:
        return []
    records = load_captures(wiki_root=wiki_root)
    states = {row["source_id"]: row["retired_json"] for row in conn.execute("SELECT source_id, retired_json FROM capture_state")}
    statuses = {row["id"]: row["status"] for row in conn.execute("SELECT id, status FROM sources")}
    return [
        replace(
            record,
            status="ingested" if record.source_id == current_source_id else statuses.get(record.source_id, record.status),
            capture=replace(record.capture, retired=json.loads(states[record.source_id]) if states.get(record.source_id) else None)
            if record.source_id in states
            else record.capture,
        )
        for record in records
    ]


def _active_ids(*, records: list[CaptureRecord], conn: sqlite3.Connection) -> set[str]:
    superseded = {row["target"] for row in conn.execute("SELECT target FROM supersessions WHERE target_type = 'source'")}
    return {
        r.source_id
        for r in records
        if r.status == "ingested" and r.capture.pinned and r.capture.retired is None and r.source_id not in superseded
    }


def validate_capture_apply(*, wiki_root: Path, source_id: str) -> None:
    """Reject ingest of a retired capture under the shared apply lock."""
    with sqlite3.connect(f"file:{wiki_root / WIKI_DIR_NAME / 'state.db'}?mode=ro", uri=True) as conn:
        row = conn.execute("SELECT original_path FROM sources WHERE id = ?", (source_id,)).fetchone()
    if row is None or not row[0].startswith("capture://"):
        return
    for record in load_captures(wiki_root=wiki_root):
        if record.source_id == source_id and record.capture.retired is not None:
            raise CaptureError(f"Capture {source_id} is retired; add a new capture instead.")


def apply_capture_effects(*, wiki_root: Path, tx: IngestTransaction, source_id: str) -> tuple[str, ...]:
    """Reconcile capture effects for every ingestion, including ordinary rewrites."""
    conn = tx.connection
    records = _records(wiki_root=wiki_root, conn=conn, current_source_id=source_id)
    current = next((record for record in records if record.source_id == source_id), None)
    warnings: list[str] = []
    if current is not None:
        cited = conn.execute("SELECT 1 FROM backrefs WHERE source_id = ? LIMIT 1", (source_id,)).fetchone()
        if (
            (current.capture.pinned or current.capture.supersedes)
            and cited is None
            and not _decision_pages(wiki_root=wiki_root, source_id=source_id, refs={})
        ):
            raise CaptureError("A pinned or superseding capture requires a cited page; prepare a new plan and retry.")
        for target in current.capture.supersedes:
            target_type = "page" if target.startswith("wiki/") else "source"
            cursor = conn.execute(
                "INSERT OR IGNORE INTO supersessions(new_source_id, target, target_type, created_at) VALUES (?, ?, ?, ?)",
                (source_id, target, target_type, current.capture.captured_at),
            )
            if cursor.rowcount:
                tx.add_inverse("DELETE FROM supersessions WHERE new_source_id = ? AND target = ?", (source_id, target))
    warnings.extend(_reconcile(wiki_root=wiki_root, tx=tx, records=records))
    return tuple(warnings)


def _reconcile(*, wiki_root: Path, tx: IngestTransaction, records: list[CaptureRecord]) -> list[str]:
    if not records:
        return []
    conn = tx.connection
    active = _active_ids(records=records, conn=conn)
    by_id = {record.source_id: record for record in records}
    refs: dict[str, set[str]] = {}
    for row in conn.execute("SELECT page_path, source_id FROM backrefs"):
        refs.setdefault(row["page_path"], set()).add(row["source_id"])
    pages = {row["path"]: row for row in conn.execute("SELECT path, pinned FROM pages")}
    page_cutoffs = {
        row["target"]: row["created_at"]
        for row in conn.execute("SELECT target, MAX(created_at) AS created_at FROM supersessions WHERE target_type='page' GROUP BY target")
    }
    # Frontmatter remains recoverable when the derived backrefs cache was rebuilt.
    for page in sorted((wiki_root / "wiki").rglob("*.md")):
        rel = page.relative_to(wiki_root).as_posix()
        if not is_semantic_page_path(rel):
            continue
        content = page.read_text()
        metadata = page_metadata(content=content)
        owners = set(_metadata_ids(metadata=metadata, key="capture_ids"))
        owner = metadata.get("capture_id")
        if isinstance(owner, str):
            owners.add(owner)
        owners.update(refs.get(rel, set()) & by_id.keys())
        if not owners:
            continue
        pinned_owners = sorted(
            owner for owner in owners & active if rel not in page_cutoffs or by_id[owner].capture.captured_at >= page_cutoffs[rel]
        )
        updates: dict[str, object] = {
            "pinned": bool(pinned_owners),
            "capture_ids": sorted(owners),
            "pinned_capture_ids": pinned_owners,
            "capture_id": pinned_owners[-1] if pinned_owners else sorted(owners)[-1],
        }
        if any(metadata.get(key) != value for key, value in updates.items()):
            tx.write_file(page, _with_metadata(content=content, updates=updates))
        row = pages.get(rel)
        desired = int(bool(pinned_owners))
        if row is not None and row["pinned"] != desired:
            conn.execute("UPDATE pages SET pinned = ? WHERE path = ?", (desired, rel))
            tx.add_inverse("UPDATE pages SET pinned = ? WHERE path = ?", (row["pinned"], rel))
    warnings = _apply_supersessions(wiki_root=wiki_root, tx=tx, records=by_id, refs=refs)
    rules = render_standing_rules(wiki_root=wiki_root, records=records, active_ids=active, refs=refs)
    rules_path = wiki_root / "wiki/standing-rules.md"
    # Do not create a generated file during ordinary ingest in a wiki with no captures.
    if records and (not rules_path.exists() or rules_path.read_text() != rules):
        tx.write_file(rules_path, rules)
    return warnings


def _decision_pages(*, wiki_root: Path, source_id: str, refs: dict[str, set[str]]) -> list[str]:
    paths = {path for path, source_ids in refs.items() if source_id in source_ids}
    for page in sorted((wiki_root / "wiki").rglob("*.md")):
        rel = page.relative_to(wiki_root).as_posix()
        if not is_semantic_page_path(rel):
            continue
        metadata = page_metadata(content=page.read_text())
        if metadata.get("capture_id") == source_id or source_id in _metadata_ids(metadata=metadata, key="capture_ids"):
            paths.add(rel)
    return sorted(path for path in paths if (wiki_root / path).is_file())


def _link(*, from_page: str, to_page: str) -> str:
    return quote(os.path.relpath(to_page, start=str(Path(from_page).parent)), safe="/.-")


def _label(*, text: str) -> str:
    return text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]").replace("\n", " ")


def _apply_supersessions(
    *, wiki_root: Path, tx: IngestTransaction, records: dict[str, CaptureRecord], refs: dict[str, set[str]]
) -> list[str]:
    conn = tx.connection
    warnings: list[str] = []
    superseded_ids = {row["target"] for row in conn.execute("SELECT target FROM supersessions WHERE target_type = 'source'")}
    retired_ids = {record.source_id for record in records.values() if record.capture.retired is not None}
    for edge in conn.execute("SELECT new_source_id, target, target_type FROM supersessions ORDER BY created_at, new_source_id").fetchall():
        record = records.get(edge["new_source_id"])
        if record is None:
            continue
        new_pages = _decision_pages(wiki_root=wiki_root, source_id=record.source_id, refs=refs)
        if not new_pages:
            continue
        if edge["target_type"] == "page":
            targets = [edge["target"]]
        else:
            targets = []
            for path, sources in refs.items():
                if edge["target"] not in sources:
                    continue
                other_live = sources - {edge["target"], record.source_id} - superseded_ids - retired_ids
                if other_live:
                    warnings.append(f"Manual review: {path} also cites other live sources; supersession not applied.")
                    existing_page = wiki_root / path
                    if existing_page.is_file():
                        content = existing_page.read_text()
                        marker = f"<!-- mdwiki-superseded: {record.source_id} -->"
                        cleaned = re.sub(r"> \*\*Superseded[^\n]*\n" + re.escape(marker) + r"\n*", "", content)
                        metadata = page_metadata(content=cleaned)
                        prior = metadata.get("superseded_by", [])
                        if isinstance(prior, list) and record.source_id in prior:
                            cleaned = _with_metadata(
                                content=cleaned, updates={"superseded_by": [sid for sid in prior if sid != record.source_id]}
                            )
                        if cleaned != content:
                            tx.write_file(existing_page, cleaned)
                else:
                    targets.append(path)
        if edge["target_type"] == "source":
            for page in (wiki_root / "wiki").rglob("*.md"):
                rel = page.relative_to(wiki_root).as_posix()
                if not is_semantic_page_path(rel):
                    continue
                recorded = page_metadata(content=page.read_text()).get("superseded_by", [])
                if isinstance(recorded, list) and record.source_id in recorded and not refs.get(rel):
                    targets.append(rel)
        for target in sorted(set(targets) - set(new_pages)):
            path = wiki_root / target
            if not path.is_file():
                raise CaptureError(f"Supersession target disappeared: {target}. Restore it and retry.")
            content = path.read_text()
            marker = f"<!-- mdwiki-superseded: {record.source_id} -->"
            callout = (
                f"> **Superseded ({record.capture.date}).** {_label(text=record.capture.title)}. "
                f"See [{_label(text=record.capture.title)}]({_link(from_page=target, to_page=new_pages[0])}).\n{marker}"
            )
            # Replace only this capture's managed block, leaving other history intact.
            pattern = re.compile(r"> \*\*Superseded[^\n]*\n" + re.escape(marker))
            if pattern.search(content):
                rewritten = pattern.sub(lambda _, replacement=callout: replacement, content)
            else:
                content = content.replace(marker, "")
                h1 = re.search(r"^# [^\n]*\n?", content, re.MULTILINE)
                offset = h1.end() if h1 else len(content) - len(strip_frontmatter(content))
                rewritten = content[:offset].rstrip() + "\n\n" + callout + "\n\n" + content[offset:].lstrip("\n")
            if rewritten != content:
                tx.write_file(path, rewritten)
            # Frontmatter preserves affected page identity through DB/backref loss.
            current = path.read_text()
            meta = page_metadata(content=current)
            superseded_by = meta.get("superseded_by", [])
            ids = list(superseded_by) if isinstance(superseded_by, list) else []
            if record.source_id not in ids:
                tx.write_file(path, _with_metadata(content=current, updates={"superseded_by": ids + [record.source_id]}))
    return warnings


def render_standing_rules(*, wiki_root: Path, records: list[CaptureRecord], active_ids: set[str], refs: dict[str, set[str]]) -> str:
    """Render the complete generated rules page without model inference."""
    lines = ["---", "generated: true", "---", "# Standing rules", ""]
    for record in sorted(records, key=lambda r: (r.capture.date, r.source_id)):
        if record.source_id not in active_ids:
            continue
        pages = [
            path
            for path in _decision_pages(wiki_root=wiki_root, source_id=record.source_id, refs=refs)
            if record.source_id in _metadata_ids(metadata=page_metadata(content=(wiki_root / path).read_text()), key="pinned_capture_ids")
        ]
        if not pages:
            continue
        lines.extend(
            [
                f"## {record.capture.title}",
                "",
                record.body,
                "",
                f"Date: {record.capture.date}. Decided by: {record.capture.decided_by or 'unspecified'}.",
                "",
                f"[{_label(text=record.capture.title)}]({_link(from_page='wiki/standing-rules.md', to_page=pages[0])})",
                "",
            ]
        )
    if len(lines) == 5:
        lines.extend(["(No active standing rules.)", ""])
    return "\n".join(lines)


def retire_capture(*, wiki_root: Path, source_id: str, reason: str) -> str:
    """Retire a capture and update all rule projections as one undoable operation."""
    if not reason.strip():
        raise CaptureError("Retirement requires a nonempty reason.")
    record = get_capture(wiki_root=wiki_root, source_id=source_id)
    with IngestTransaction(
        wiki_root=wiki_root, source_id=None, summary=f"capture retire {record.original_path} → {' '.join(reason.split())}"
    ) as tx:
        record = get_capture(wiki_root=wiki_root, source_id=record.source_id)
        if record.capture.retired is not None:
            raise CaptureError(f"Capture {record.source_id} is already retired.")
        conn = tx.connection
        previous = conn.execute("SELECT retired_json FROM capture_state WHERE source_id = ?", (record.source_id,)).fetchone()
        retired = {"date": datetime.now(tz=UTC).date().isoformat(), "reason": reason}
        conn.execute(
            "INSERT INTO capture_state(source_id, retired_json) VALUES (?, ?) ON CONFLICT(source_id) DO UPDATE SET retired_json=excluded.retired_json",
            (record.source_id, json.dumps(retired)),
        )
        if previous is None:
            # Persist explicit null on undo, overriding the mirrored retirement in sidecar.
            tx.add_inverse("UPDATE capture_state SET retired_json = NULL WHERE source_id = ?", (record.source_id,))
        else:
            tx.add_inverse("UPDATE capture_state SET retired_json = ? WHERE source_id = ?", (previous["retired_json"], record.source_id))
        _reconcile(wiki_root=wiki_root, tx=tx, records=_records(wiki_root=wiki_root, conn=conn))
        from mdwiki.index import build_index

        tx.write_file(wiki_root / "wiki/index.md", build_index(wiki_root))
    mirror_capture_state(wiki_root=wiki_root)
    return f"Retired capture {record.source_id}: {reason}"


def mirror_capture_state(*, wiki_root: Path, lock_held: bool = False) -> None:
    """Serialize lifecycle metadata with capture registration and exports."""
    if lock_held:
        _mirror_capture_state_locked(wiki_root=wiki_root)
        return
    lock = _acquire_write_lock(wiki_root)
    try:
        _mirror_capture_state_locked(wiki_root=wiki_root)
    finally:
        _release_write_lock(lock)


def _mirror_capture_state_locked(*, wiki_root: Path) -> None:
    """Mirror only lifecycle fields, preserving later registrations and export metadata."""
    path = wiki_root / "raw/.sources.json"
    if not path.is_file():
        return
    sidecar = json.loads(path.read_text())
    with connect(wiki_root / WIKI_DIR_NAME / "state.db") as conn:
        rows = conn.execute("SELECT source_id, retired_json FROM capture_state").fetchall()
    for row in rows:
        if row["source_id"] in sidecar:
            sidecar[row["source_id"]]["capture"]["retired"] = json.loads(row["retired_json"]) if row["retired_json"] else None
    _write_registration_sidecar(path, sidecar)

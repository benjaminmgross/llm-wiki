"""Immutable, provenance-bearing internal sources captured from a session."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
import uuid
from collections.abc import Mapping
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from datetime import date as calendar_date
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

import yaml

from mdwiki.init import SidecarCorruptError, _slugify, _write_registration_sidecar
from mdwiki.semantic_pages import is_semantic_page_path
from mdwiki.state import connect
from mdwiki.transaction import _acquire_write_lock, _release_write_lock

type CaptureKind = Literal["standing-rule", "decision", "fact", "correction"]
CAPTURE_KINDS: tuple[CaptureKind, ...] = ("standing-rule", "decision", "fact", "correction")


class CaptureError(ValueError):
    """A capture input, identifier, or destination cannot be used safely."""


class CaptureIngestError(CaptureError):
    """Registration succeeded but ingestion needs a retry."""

    def __init__(self, *, source_id: str, detail: str) -> None:
        self.source_id = source_id
        super().__init__(
            f"Capture {source_id} remains pending: {detail}. Retry with `mdwiki ingest --pending`, or session-ingest prepare/apply."
        )


@dataclass(frozen=True, kw_only=True)
class CaptureInput:
    """Conclusion and authority supplied at the capture boundary."""

    kind: CaptureKind
    title: str
    body: str
    decided_by: str = ""
    date: str = field(default_factory=lambda: calendar_date.today().isoformat())
    session: str = ""
    context: str = ""
    rationale: str = ""
    supersedes: tuple[str, ...] = ()
    pinned: bool | None = None


@dataclass(frozen=True, kw_only=True)
class CaptureMetadata:
    """Durable capture provenance and mutable lifecycle metadata."""

    kind: CaptureKind
    title: str
    decided_by: str
    date: str
    session: str
    context: str
    rationale: str
    supersedes: tuple[str, ...]
    pinned: bool
    retired: dict[str, str] | None
    export_path: str | None
    capture_uuid: str
    captured_at: str
    body_length: int


@dataclass(frozen=True, kw_only=True)
class CaptureRecord:
    """One registered capture, including its immutable raw rendering."""

    source_id: str
    original_path: str
    raw_path: str
    status: str
    capture: CaptureMetadata
    body: str
    raw_text: str


def is_capture_source(row: Mapping[str, object]) -> bool:
    """Recognize internal captures without interpreting their labels as paths."""
    original_path = row.get("original_path")
    return row.get("origin") == "capture" or (isinstance(original_path, str) and original_path.startswith("capture://"))


def capture_kind_for_source(*, wiki_root: Path, source_id: str) -> CaptureKind | None:
    """Return capture guidance only for a registered internal source."""
    db_path = wiki_root / ".mdwiki/state.db"
    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
        row = conn.execute("SELECT original_path FROM sources WHERE id = ?", (source_id,)).fetchone()
    if row is None or not row[0].startswith("capture://"):
        return None
    return get_capture(wiki_root=wiki_root, source_id=source_id).capture.kind


def register_capture(*, wiki_root: Path, capture: CaptureInput) -> CaptureRecord:
    """Publish an immutable raw source and its recovery metadata under the write lock."""
    _check_input(capture=capture)
    wiki_root = wiki_root.resolve()
    db_path = wiki_root / ".mdwiki/state.db"
    if not db_path.is_file():
        raise CaptureError("No initialized wiki; run `mdwiki init` first.")
    lock = _acquire_write_lock(wiki_root)
    try:
        sidecar_path = wiki_root / "raw/.sources.json"
        sidecar = _load_sidecar(wiki_root=wiki_root)
        prior_sidecar = sidecar_path.read_bytes() if sidecar_path.exists() else None
        with closing(connect(db_path)) as conn:
            targets = _resolve_targets(wiki_root=wiki_root, targets=capture.supersedes, conn=conn)
            now = time.time()
            metadata = CaptureMetadata(
                kind=capture.kind,
                title=capture.title,
                decided_by=capture.decided_by,
                date=capture.date,
                session=capture.session,
                context=capture.context,
                rationale=capture.rationale,
                supersedes=targets,
                pinned=capture.pinned if capture.pinned is not None else capture.kind == "standing-rule",
                retired=None,
                export_path=None,
                capture_uuid=str(uuid.uuid4()),
                captured_at=datetime.fromtimestamp(now, tz=UTC).isoformat(),
                body_length=len(capture.body),
            )
            text = render_capture(capture=metadata, body=capture.body)
            content_hash = hashlib.sha256(text.encode()).hexdigest()
            source_id = content_hash[:12]
            slug = _slugify(capture.title)
            original_path = f"capture://{capture.date}/{slug}-{source_id[:8]}"
            raw_path = f"raw/{source_id}-{slug}.md"
            raw_file = wiki_root / raw_path
            if raw_file.exists() or source_id in sidecar or conn.execute("SELECT 1 FROM sources WHERE id = ?", (source_id,)).fetchone():
                raise CaptureError("Capture identity collision; retry registration.")
            sidecar[source_id] = {
                "original_path": original_path,
                "raw_path": raw_path,
                "content_hash": content_hash,
                "mtime": now,
                "loader": "capture",
                "origin": "capture",
                "status": "pending",
                "ingested_at": None,
                "failure_reason": None,
                "capture": asdict(metadata),
            }
            created_raw = False
            sidecar_written = False
            try:
                with raw_file.open("x", encoding="utf-8", newline="") as handle:
                    created_raw = True
                    handle.write(text)
                conn.execute(
                    "INSERT INTO sources (id, original_path, raw_path, content_hash, mtime, status) VALUES (?, ?, ?, ?, ?, 'pending')",
                    (source_id, original_path, raw_path, content_hash, now),
                )
                _write_registration_sidecar(sidecar_path, sidecar)
                sidecar_written = True
                conn.commit()
            except Exception:
                conn.rollback()
                if created_raw:
                    raw_file.unlink(missing_ok=True)
                if sidecar_written:
                    if prior_sidecar is None:
                        sidecar_path.unlink(missing_ok=True)
                    else:
                        _atomic_write(path=sidecar_path, text=prior_sidecar.decode())
                raise
            return CaptureRecord(
                source_id=source_id,
                original_path=original_path,
                raw_path=raw_path,
                status="pending",
                capture=metadata,
                body=capture.body,
                raw_text=text,
            )
    finally:
        _release_write_lock(lock)


def render_capture(*, capture: CaptureMetadata, body: str) -> str:
    """Render quotable markdown without a frontmatter block."""
    sections = [f"# {capture.title}", f"## Conclusion\n{body}"]
    if capture.rationale:
        sections.append(f"## Rationale\n{capture.rationale}")
    sections.append(
        f"## Provenance\nKind: {capture.kind}. Decided by: {capture.decided_by}. Date: {capture.date}. "
        f"Session: {capture.session}. Context: {capture.context}. Captured at: {capture.captured_at}. Capture id: {capture.capture_uuid}."
    )
    if capture.supersedes:
        sections.append("## Supersedes\n" + "\n".join(f"- {target}" for target in capture.supersedes))
    return "\n\n".join(sections) + "\n"


def load_captures(*, wiki_root: Path) -> list[CaptureRecord]:
    """Read captures, overlaying current cached status and retirement when available."""
    sidecar = _load_sidecar(wiki_root=wiki_root)
    statuses: dict[str, str] = {}
    retired: dict[str, dict[str, str] | None] = {}
    db_path = wiki_root / ".mdwiki/state.db"
    if db_path.is_file():
        with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as conn:
            statuses = dict(conn.execute("SELECT id, status FROM sources"))
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='capture_state'").fetchone():
                retired = {
                    source_id: _parse_retired(value=json.loads(value) if value is not None else None)
                    for source_id, value in conn.execute("SELECT source_id, retired_json FROM capture_state")
                }
    records: list[CaptureRecord] = []
    for source_id, entry in sorted(sidecar.items()):
        if not is_capture_source(entry):
            continue
        data = entry.get("capture")
        if not isinstance(data, dict):
            raise SidecarCorruptError(f"Capture {source_id} has no capture metadata; restore raw/.sources.json.")
        parsed_data = dict(data)
        if source_id in retired:
            parsed_data["retired"] = retired[source_id]
        metadata = _parse_metadata(data=parsed_data)
        raw_path = _require_text(data=entry, key="raw_path")
        raw_file = (wiki_root / raw_path).resolve()
        if not raw_file.is_relative_to((wiki_root / "raw").resolve()):
            raise SidecarCorruptError(f"Capture {source_id} raw_path escapes raw/.")
        raw_text = raw_file.read_bytes().decode("utf-8") if raw_file.is_file() else ""
        prefix = f"# {metadata.title}\n\n## Conclusion\n"
        body = raw_text[len(prefix) : len(prefix) + metadata.body_length] if raw_text.startswith(prefix) else ""
        records.append(
            CaptureRecord(
                source_id=source_id,
                original_path=_require_text(data=entry, key="original_path"),
                raw_path=raw_path,
                status=statuses.get(source_id, str(entry.get("status", "pending"))),
                capture=metadata,
                body=body,
                raw_text=raw_text,
            )
        )
    return records


def get_capture(*, wiki_root: Path, source_id: str) -> CaptureRecord:
    """Resolve an exact capture id or an unambiguous nonempty prefix."""
    if not source_id:
        raise CaptureError("A capture id is required.")
    matches = [record for record in load_captures(wiki_root=wiki_root) if record.source_id.startswith(source_id)]
    if len(matches) != 1:
        raise CaptureError(f"{'Ambiguous' if matches else 'Unknown'} capture id: {source_id!r}.")
    return matches[0]


def export_capture(*, wiki_root: Path, source_id: str, path: Path) -> Path:
    """Export a marked copy without overwriting sources or managed wiki files."""
    wiki_root = wiki_root.resolve()
    lock = _acquire_write_lock(wiki_root)
    try:
        record = get_capture(wiki_root=wiki_root, source_id=source_id)
        if not record.raw_text:
            raise CaptureError(f"Capture {record.source_id} raw source is missing.")
        destination = (path if path.is_absolute() else wiki_root / path).resolve()
        _check_export_destination(wiki_root=wiki_root, destination=destination)
        text = _export_text(record=record)
        if destination.exists() and (not destination.is_file() or destination.read_bytes() != text.encode("utf-8")):
            raise CaptureError(f"Export destination already exists: {destination}.")
        sidecar = _load_sidecar(wiki_root=wiki_root)
        metadata = cast(dict[str, object], sidecar[record.source_id]["capture"])
        metadata["export_path"] = str(destination.relative_to(wiki_root)) if destination.is_relative_to(wiki_root) else str(destination)
        existed = destination.exists()
        destination.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path=destination, text=text)
        try:
            _write_registration_sidecar(wiki_root / "raw/.sources.json", sidecar)
        except Exception:
            if not existed:
                destination.unlink(missing_ok=True)
            raise
        return destination
    finally:
        _release_write_lock(lock)


def capture_export_state(*, wiki_root: Path, record: CaptureRecord) -> str:
    """Describe the optional human-readable export without changing it."""
    if record.capture.export_path is None:
        return "not-exported"
    path = Path(record.capture.export_path)
    path = path if path.is_absolute() else wiki_root / path
    if not path.is_file():
        return "missing"
    return "current" if path.read_bytes() == _export_text(record=record).encode("utf-8") else "out-of-date"


def _check_input(*, capture: CaptureInput) -> None:
    if capture.kind not in CAPTURE_KINDS:
        raise CaptureError(f"Unknown capture kind: {capture.kind!r}.")
    if not capture.title.strip() or "\n" in capture.title or "\r" in capture.title:
        raise CaptureError("A nonempty single-line title is required.")
    if not capture.body.strip():
        raise CaptureError("A nonempty conclusion body is required.")
    if capture.kind in {"standing-rule", "decision"} and not capture.decided_by.strip():
        raise CaptureError("--decided-by is required for standing-rule and decision captures.")
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", capture.date):
            raise ValueError("date format")
        calendar_date.fromisoformat(capture.date)
    except ValueError as exc:
        raise CaptureError("Capture date must be a valid YYYY-MM-DD date.") from exc


def _resolve_targets(*, wiki_root: Path, targets: tuple[str, ...], conn: sqlite3.Connection) -> tuple[str, ...]:
    resolved: list[str] = []
    for target in targets:
        pure = PurePosixPath(target)
        if pure.is_absolute() or ".." in pure.parts or "\\" in target:
            raise CaptureError(f"Unsafe supersession target: {target!r}.")
        if target.startswith("wiki/"):
            full = wiki_root / pure
            if (
                target == "wiki/standing-rules.md"
                or not is_semantic_page_path(target)
                or not full.is_file()
                or full.is_symlink()
                or not full.resolve().is_relative_to(wiki_root / "wiki")
            ):
                raise CaptureError(f"Supersession target must be an existing semantic wiki page: {target!r}.")
            frontmatter = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", full.read_text(), re.DOTALL)
            if frontmatter is not None:
                try:
                    metadata = yaml.safe_load(frontmatter.group(1))
                except yaml.YAMLError as exc:
                    raise CaptureError(f"Invalid frontmatter on supersession target: {target!r}.") from exc
                if isinstance(metadata, dict) and metadata.get("generated") is True:
                    raise CaptureError(f"Supersession target is a generated page: {target!r}.")
            canonical = pure.as_posix()
        else:
            if not re.fullmatch(r"[0-9a-f]{1,12}", target):
                raise CaptureError(f"Unknown source target: {target!r}.")
            rows = conn.execute("SELECT id FROM sources WHERE id LIKE ?", (target + "%",)).fetchall()
            if len(rows) != 1:
                raise CaptureError(f"{'Ambiguous' if rows else 'Unknown'} source target: {target!r}.")
            canonical = rows[0]["id"]
        if canonical not in resolved:
            resolved.append(canonical)
    return tuple(resolved)


def _load_sidecar(*, wiki_root: Path) -> dict[str, dict[str, Any]]:
    path = wiki_root / "raw/.sources.json"
    if not path.exists():
        return {}
    try:
        parsed: object = json.loads(path.read_text())
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise SidecarCorruptError("raw/.sources.json is not valid JSON; restore it from version control.") from exc
    if not isinstance(parsed, dict) or any(not isinstance(key, str) or not isinstance(value, dict) for key, value in parsed.items()):
        raise SidecarCorruptError("raw/.sources.json must map source ids to metadata objects; restore it from version control.")
    return parsed


def _parse_metadata(*, data: Mapping[str, object]) -> CaptureMetadata:
    kind = _require_text(data=data, key="kind")
    supersedes = data.get("supersedes", [])
    if kind not in CAPTURE_KINDS or not isinstance(supersedes, (list, tuple)) or any(not isinstance(value, str) for value in supersedes):
        raise SidecarCorruptError("Invalid capture kind or supersedes metadata in raw/.sources.json.")
    pinned = data.get("pinned")
    body_length = data.get("body_length")
    export_path = data.get("export_path")
    if (
        not isinstance(pinned, bool)
        or not isinstance(body_length, int)
        or body_length < 0
        or (export_path is not None and not isinstance(export_path, str))
    ):
        raise SidecarCorruptError("Invalid capture pin, body length, or export metadata in raw/.sources.json.")
    return CaptureMetadata(
        kind=cast(CaptureKind, kind),
        title=_require_text(data=data, key="title"),
        decided_by=_require_text(data=data, key="decided_by"),
        date=_require_text(data=data, key="date"),
        session=_require_text(data=data, key="session"),
        context=_require_text(data=data, key="context"),
        rationale=_require_text(data=data, key="rationale"),
        supersedes=tuple(supersedes),
        pinned=pinned,
        retired=_parse_retired(value=data.get("retired")),
        export_path=export_path,
        capture_uuid=_require_text(data=data, key="capture_uuid"),
        captured_at=_require_text(data=data, key="captured_at"),
        body_length=body_length,
    )


def _parse_retired(*, value: object) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()):
        raise SidecarCorruptError("Invalid capture retirement metadata in raw/.sources.json.")
    return value


def _require_text(*, data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise SidecarCorruptError(f"Invalid capture {key} metadata in raw/.sources.json.")
    return value


def _check_export_destination(*, wiki_root: Path, destination: Path) -> None:
    if any(destination.is_relative_to((wiki_root / folder).resolve()) for folder in ("raw", "wiki", ".mdwiki")):
        raise CaptureError("Exports must be outside raw/, wiki/, and .mdwiki/.")
    with closing(connect(wiki_root / ".mdwiki/state.db")) as conn:
        for row in conn.execute("SELECT original_path FROM sources"):
            original = row["original_path"]
            if not original.startswith("capture://") and (wiki_root / original).resolve() == destination:
                raise CaptureError("An export cannot replace a registered source.")


def _export_text(*, record: CaptureRecord) -> str:
    return f"<!-- mdwiki-capture: {record.source_id} -->\n{record.raw_text}"


def _atomic_write(*, path: Path, text: str) -> None:
    handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="", dir=path.parent, prefix=f"{path.name}.tmp-", delete=False)
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)

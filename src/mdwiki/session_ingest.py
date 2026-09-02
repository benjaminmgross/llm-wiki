"""Native-session plan handoff for multi-agent corpus ingestion.

This module deliberately performs no model or embedding inference. The active
Codex or Claude Code parent session owns sub-agent delegation; mdwiki provides
only deterministic source assignment, context capture, validation, and apply.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mdwiki.chunker import MarkdownChunker
from mdwiki.discover import WIKI_DIR_NAME
from mdwiki.ingest import IngestResult, _chunk_or_whole, _recent_log_entries, _resolve_source, apply_ingest_plan
from mdwiki.ingest_tool import INGEST_TOOL_DEFINITION
from mdwiki.plan import Plan, allowed_kinds_for_wiki, parse_plan_dict
from mdwiki.quote import min_quote_words_for_wiki, quote_normalize_mode_for_wiki, verify_plan
from mdwiki.search import lexical_candidates_for_text
from mdwiki.semantic_pages import is_semantic_page_path

SESSION_ENVELOPE_VERSION: int = 1
SESSION_AGENT_INSTRUCTIONS: str = (
    "Analyze only this assigned source using the active Codex or Claude Code session. "
    "Remain read-only, inspect current wiki pages as needed, and fill only the plan field. "
    "Start from wiki.lexical_candidates and run `mdwiki search \"<name or term>\"` for every named subject before proposing a "
    "new page; update an existing page when one exists. "
    "Do not call model APIs, mdwiki providers, or local inference. Every claim must quote the source verbatim."
)


class PlanInvalidatedError(RuntimeError):
    """Raised when wiki state changed after a session plan was prepared."""


class SessionPlanError(ValueError):
    """Raised when a session envelope or plan is malformed."""


@dataclass(frozen=True)
class SessionSource:
    """One source eligible for unique assignment to a session sub-agent."""

    source_id: str
    original_path: str
    raw_path: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.source_id, "original_path": self.original_path, "raw_path": self.raw_path}


@dataclass(frozen=True)
class PreparedSessionPlan:
    """JSON-serializable analysis context and optimistic freshness snapshot."""

    source: SessionSource
    source_text: str
    sections: tuple[dict[str, Any], ...]
    schema_text: str
    recent_log_entries: tuple[str, ...]
    config_sha256: str
    schema_sha256: str
    source_sha256: str
    page_sha256: dict[str, str]
    lexical_candidates: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "version": SESSION_ENVELOPE_VERSION,
            "source": {
                **self.source.to_dict(),
                "text": self.source_text,
                "sections": list(self.sections),
            },
            "wiki": {
                "schema": self.schema_text,
                "recent_log_entries": list(self.recent_log_entries),
                "page_paths": sorted(self.page_sha256),
                "lexical_candidates": list(self.lexical_candidates),
            },
            "agent_instructions": SESSION_AGENT_INSTRUCTIONS,
            "plan_contract": INGEST_TOOL_DEFINITION["input_schema"],
            "snapshot": {
                "config_sha256": self.config_sha256,
                "schema_sha256": self.schema_sha256,
                "source_sha256": self.source_sha256,
                "pages": dict(sorted(self.page_sha256.items())),
            },
            "plan": None,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


def list_session_sources(wiki_root: Path) -> list[SessionSource]:
    """Return pending and failed sources once each in deterministic order."""
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, original_path, raw_path FROM sources WHERE status IN ('pending', 'failed') ORDER BY original_path, id"
        ).fetchall()
    return [SessionSource(source_id=row["id"], original_path=row["original_path"], raw_path=row["raw_path"]) for row in rows]


def prepare_session_plan(wiki_root: Path, source_id_or_path: str) -> PreparedSessionPlan:
    """Capture read-only context for one native session sub-agent."""
    source_row = _resolve_source(wiki_root, source_id_or_path, read_only=True)
    raw_path = wiki_root / source_row["raw_path"]
    source_bytes = raw_path.read_bytes()
    source_text = source_bytes.decode()
    sections = _chunk_or_whole(
        raw_path,
        source_path=source_row["original_path"],
        chunker=MarkdownChunker(min_section_words=1),
    )
    schema_path = wiki_root / WIKI_DIR_NAME / "schema.md"
    schema_bytes = schema_path.read_bytes()
    config_bytes = (wiki_root / WIKI_DIR_NAME / "config.toml").read_bytes()
    return PreparedSessionPlan(
        source=SessionSource(
            source_id=source_row["id"],
            original_path=source_row["original_path"],
            raw_path=source_row["raw_path"],
        ),
        source_text=source_text,
        sections=tuple(sections),
        schema_text=schema_bytes.decode(),
        recent_log_entries=tuple(_recent_log_entries(wiki_root, limit=10)),
        config_sha256=_sha256(config_bytes),
        schema_sha256=_sha256(schema_bytes),
        source_sha256=_sha256(source_bytes),
        page_sha256=_snapshot_pages(wiki_root),
        lexical_candidates=lexical_candidates_for_text(wiki_root, source_text),
    )


def apply_session_plan(wiki_root: Path, payload: dict[str, object]) -> IngestResult:
    """Validate and apply a session-produced plan without model inference."""
    _require_version(payload)
    source_payload = _require_dict(payload, "source")
    snapshot_payload = _require_dict(payload, "snapshot")
    plan_payload = _require_dict(payload, "plan")
    source_id = _require_str(source_payload, "id")
    original_path = _require_str(source_payload, "original_path")
    raw_path = _require_str(source_payload, "raw_path")
    source_row = _resolve_source(wiki_root, source_id, read_only=True)
    if source_row["original_path"] != original_path or source_row["raw_path"] != raw_path:
        raise SessionPlanError("Session envelope source identity does not match the registered source.")

    config_sha256 = _require_str(snapshot_payload, "config_sha256")
    schema_sha256 = _require_str(snapshot_payload, "schema_sha256")
    source_sha256 = _require_str(snapshot_payload, "source_sha256")
    page_sha256 = _require_str_map(snapshot_payload, "pages")
    _validate_config_snapshot(wiki_root=wiki_root, config_sha256=config_sha256)
    plan = parse_plan_dict(plan_payload, allowed_kinds=allowed_kinds_for_wiki(wiki_root))
    _verify_source_claims(wiki_root=wiki_root, source_row=source_row, plan=plan)

    def validate_locked() -> None:
        _validate_latest_state(
            wiki_root=wiki_root,
            source_row=source_row,
            plan=plan,
            config_sha256=config_sha256,
            schema_sha256=schema_sha256,
            source_sha256=source_sha256,
            page_sha256=page_sha256,
        )

    return apply_ingest_plan(
        wiki_root,
        source_id=source_id,
        original_path=original_path,
        plan=plan,
        embedder=None,
        validate_locked=validate_locked,
    )


def _verify_source_claims(*, wiki_root: Path, source_row: dict[str, Any], plan: Plan) -> None:
    raw_path = wiki_root / source_row["raw_path"]
    try:
        source_text = raw_path.read_text()
        sections = _chunk_or_whole(
            raw_path,
            source_path=source_row["original_path"],
            chunker=MarkdownChunker(min_section_words=1),
        )
    except (OSError, UnicodeError) as exc:
        raise PlanInvalidatedError("Registered source disappeared or became unreadable after plan preparation; prepare and retry.") from exc
    verification = verify_plan(
        plan,
        source_text=source_text,
        section_ids={section["section_id"] for section in sections},
        mode=quote_normalize_mode_for_wiki(wiki_root),
        min_quote_words=min_quote_words_for_wiki(wiki_root),
    )
    if not verification.valid:
        details = "; ".join(error.message for error in verification.errors)
        raise SessionPlanError(f"Quote verification failed: {details}")


def _validate_latest_state(
    *,
    wiki_root: Path,
    source_row: dict[str, Any],
    plan: Plan,
    config_sha256: str,
    schema_sha256: str,
    source_sha256: str,
    page_sha256: dict[str, str],
) -> None:
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        row = conn.execute("SELECT status FROM sources WHERE id = ?", (source_row["id"],)).fetchone()
    if row is None:
        raise PlanInvalidatedError("Registered source disappeared after plan preparation; rebuild, prepare, and retry.")
    if row[0] == "ingested":
        raise PlanInvalidatedError("Registered source is already ingested; do not apply the same session envelope twice.")
    _validate_config_snapshot(wiki_root=wiki_root, config_sha256=config_sha256)
    schema_path = wiki_root / WIKI_DIR_NAME / "schema.md"
    if not schema_path.is_file():
        raise PlanInvalidatedError("Wiki schema disappeared after plan preparation; restore it, prepare, and retry.")
    if _hash_file(schema_path) != schema_sha256:
        raise PlanInvalidatedError("Wiki schema changed after plan preparation; prepare and retry the source.")
    raw_path = wiki_root / source_row["raw_path"]
    if not raw_path.is_file():
        raise PlanInvalidatedError("Registered source disappeared after plan preparation; refresh, prepare, and retry the source.")
    if _hash_file(raw_path) != source_sha256:
        raise PlanInvalidatedError("Registered source changed after plan preparation; refresh, prepare, and retry the source.")

    for update in plan.updates:
        expected = page_sha256.get(update.page)
        target = wiki_root / update.page
        if expected is None:
            raise PlanInvalidatedError(f"Update target {update.page} was absent from the analysis snapshot; prepare and retry.")
        if not target.is_file() or _hash_file(target) != expected:
            raise PlanInvalidatedError(f"Update target {update.page} changed after plan preparation; prepare and retry.")
    for new_page in plan.new_pages:
        if new_page.path in page_sha256 or (wiki_root / new_page.path).exists():
            raise PlanInvalidatedError(f"New-page target {new_page.path} changed after plan preparation; prepare and retry.")
    planned_new_pages = {new_page.path for new_page in plan.new_pages}
    explicit_write_paths = planned_new_pages | {update.page for update in plan.updates}
    for contradiction in plan.contradictions:
        if contradiction.page not in explicit_write_paths:
            _require_fresh_cross_ref_endpoint(wiki_root, page_sha256, contradiction.page)
    for cross_ref in plan.cross_refs:
        if cross_ref.from_page not in explicit_write_paths:
            _require_fresh_cross_ref_endpoint(wiki_root, page_sha256, cross_ref.from_page)
        if cross_ref.to_page not in explicit_write_paths and not (wiki_root / cross_ref.to_page).is_file():
            raise PlanInvalidatedError(
                f"Cross-reference endpoint {cross_ref.to_page} disappeared after plan preparation; prepare and retry."
            )


def _require_fresh_cross_ref_endpoint(wiki_root: Path, page_sha256: dict[str, str], endpoint: str) -> None:
    expected = page_sha256.get(endpoint)
    target = wiki_root / endpoint
    if expected is None or not target.is_file() or _hash_file(target) != expected:
        raise PlanInvalidatedError(f"Cross-reference endpoint {endpoint} changed after plan preparation; prepare and retry.")


def _snapshot_pages(wiki_root: Path) -> dict[str, str]:
    wiki_dir = wiki_root / "wiki"
    if not wiki_dir.is_dir():
        return {}
    paths = sorted(
        path
        for path in wiki_dir.rglob("*")
        if path.is_file() and is_semantic_page_path(path.relative_to(wiki_root).as_posix())
    )
    return {path.relative_to(wiki_root).as_posix(): _hash_file(path) for path in paths}


def _validate_config_snapshot(*, wiki_root: Path, config_sha256: str) -> None:
    """Map missing or changed plan policy to the retryable invalidation contract."""
    config_path = wiki_root / WIKI_DIR_NAME / "config.toml"
    if not config_path.is_file():
        raise PlanInvalidatedError("Wiki configuration disappeared after plan preparation; restore it, prepare, and retry.")
    if _hash_file(config_path) != config_sha256:
        raise PlanInvalidatedError("Wiki configuration changed after plan preparation; prepare and retry the source.")


def _hash_file(path: Path) -> str:
    return _sha256(path.read_bytes())


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _require_version(payload: dict[str, object]) -> None:
    if payload.get("version") != SESSION_ENVELOPE_VERSION:
        raise SessionPlanError(f"Unsupported session envelope version: {payload.get('version')!r}.")


def _require_dict(payload: dict[str, object], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise SessionPlanError(f"Session envelope field {key!r} must be an object.")
    return value


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise SessionPlanError(f"Session envelope field {key!r} must be a string.")
    return value


def _require_str_map(payload: dict[str, Any], key: str) -> dict[str, str]:
    value = payload.get(key)
    if not isinstance(value, dict) or not all(isinstance(path, str) and isinstance(digest, str) for path, digest in value.items()):
        raise SessionPlanError(f"Session envelope field {key!r} must be a string-to-string object.")
    return value

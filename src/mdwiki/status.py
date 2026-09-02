"""Read-only summary of wiki state — pure data + a formatter for the CLI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from mdwiki.discover import WIKI_DIR_NAME
from mdwiki.page_kinds import iter_wiki_page_paths
from mdwiki.state import connect


@dataclass(frozen=True)
class EventRow:
    """One row from the ``events`` table — read-only view for status display."""

    ts: float
    kind: str
    summary: str | None


@dataclass(frozen=True)
class FailedSourceRow:
    """One retryable source failure shown by ``mdwiki status``."""

    original_path: str
    reason: str


@dataclass(frozen=True)
class StatusReport:
    """A snapshot of wiki state suitable for ``mdwiki status``."""

    pending: int
    ingested: int
    pages_total: int
    failed: int = 0
    failed_sources: tuple[FailedSourceRow, ...] = ()
    disk_pages_total: int = 0
    pages_with_embeddings: int = 0
    drift_warnings: tuple[str, ...] = ()
    pages_by_kind: dict[str, int] = field(default_factory=dict)
    events_total: int = 0
    last_lint_ts: float | None = None
    recent_events: tuple[EventRow, ...] = ()
    contradictions_pending: int = 0


def get_status(wiki_root: Path, *, recent_event_limit: int = 5) -> StatusReport:
    """Build a ``StatusReport`` for the wiki rooted at ``wiki_root``.

    Parameters
    ----------
    wiki_root : Path
        Folder containing ``.mdwiki/``.
    recent_event_limit : int, optional
        Cap on the number of recent events to include (default 5).
    """
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    with connect(db_path) as conn:
        pending = conn.execute("SELECT COUNT(*) AS c FROM sources WHERE status = 'pending'").fetchone()["c"]
        ingested = conn.execute("SELECT COUNT(*) AS c FROM sources WHERE status = 'ingested'").fetchone()["c"]
        failed_rows = conn.execute(
            "SELECT original_path, failure_reason FROM sources WHERE status = 'failed' ORDER BY original_path"
        ).fetchall()
        failed_sources = tuple(
            FailedSourceRow(
                original_path=row["original_path"],
                reason=row["failure_reason"] or "reason unavailable",
            )
            for row in failed_rows
        )

        pages_total = conn.execute("SELECT COUNT(*) AS c FROM pages").fetchone()["c"]
        pages_with_embeddings = conn.execute("SELECT COUNT(*) AS c FROM pages WHERE embedding IS NOT NULL").fetchone()["c"]
        embeddable_pages = conn.execute("SELECT COUNT(*) AS c FROM pages WHERE kind != 'source'").fetchone()["c"]
        kind_rows = conn.execute("SELECT kind, COUNT(*) AS c FROM pages GROUP BY kind").fetchall()
        pages_by_kind = {row["kind"]: row["c"] for row in kind_rows}

        contradictions_pending = conn.execute("SELECT COUNT(*) AS c FROM contradictions WHERE resolution = 'pending'").fetchone()["c"]

        events_total = conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]
        lint_row = conn.execute("SELECT MAX(ts) AS ts FROM events WHERE kind = 'lint'").fetchone()
        last_lint_ts = lint_row["ts"] if lint_row and lint_row["ts"] is not None else None

        recent_rows = conn.execute(
            "SELECT ts, kind, summary FROM events ORDER BY ts DESC LIMIT ?",
            (recent_event_limit,),
        ).fetchall()
        recent_events = tuple(EventRow(ts=r["ts"], kind=r["kind"], summary=r["summary"]) for r in recent_rows)

    disk_pages_total = len(iter_wiki_page_paths(wiki_root))
    drift_warnings = tuple(
        _build_drift_warnings(
            disk_pages_total=disk_pages_total,
            pages_total=pages_total,
            pages_with_embeddings=pages_with_embeddings,
            embeddable_pages=embeddable_pages,
        )
    )

    return StatusReport(
        pending=pending,
        ingested=ingested,
        pages_total=pages_total,
        failed=len(failed_sources),
        failed_sources=failed_sources,
        disk_pages_total=disk_pages_total,
        pages_with_embeddings=pages_with_embeddings,
        drift_warnings=drift_warnings,
        pages_by_kind=pages_by_kind,
        events_total=events_total,
        last_lint_ts=last_lint_ts,
        recent_events=recent_events,
        contradictions_pending=contradictions_pending,
    )


def format_status(report: StatusReport, *, wiki_root: Path) -> str:
    """Render ``StatusReport`` for human consumption (used by the CLI)."""
    lines: list[str] = [f"mdwiki at {wiki_root}", ""]

    lines.append(f"Sources:    {report.pending} pending, {report.failed} failed, {report.ingested} ingested")

    if report.failed_sources:
        lines.append("Failed sources (retried by `mdwiki ingest --pending`):")
        for source in report.failed_sources:
            lines.append(f"  - {source.original_path}: {source.reason}")

    kind_str = ", ".join(f"{k}: {v}" for k, v in sorted(report.pages_by_kind.items())) or "—"
    lines.append(f"Wiki pages: {report.pages_total}  ({kind_str})")
    lines.append(f"Disk pages: {report.disk_pages_total}; page embeddings: {report.pages_with_embeddings}")

    lines.append(f"Contradictions: {report.contradictions_pending} pending")
    lines.append(f"Events:     {report.events_total}")

    last_lint = "never" if report.last_lint_ts is None else _fmt_ts(report.last_lint_ts)
    lines.append(f"Last lint:  {last_lint}")

    lines.append("")
    if report.recent_events:
        lines.append(f"Recent events (last {len(report.recent_events)}):")
        for ev in report.recent_events:
            summary = ev.summary or "(no summary)"
            lines.append(f"  {_fmt_ts(ev.ts)}  [{ev.kind}]  {summary}")
    else:
        lines.append("Recent events: (none)")

    if report.drift_warnings:
        lines.append("")
        lines.extend(report.drift_warnings)

    return "\n".join(lines)


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d %H:%M:%SZ")


def _build_drift_warnings(
    *,
    disk_pages_total: int,
    pages_total: int,
    pages_with_embeddings: int,
    embeddable_pages: int | None = None,
) -> list[str]:
    if embeddable_pages is None:
        embeddable_pages = pages_total
    warnings: list[str] = []
    if disk_pages_total > 0 and pages_total == 0:
        warnings.append("WARNING: wiki files exist on disk, but state.db has no page rows.")
        warnings.append("Run `mdwiki rebuild --pages` before ingesting.")
        return warnings
    if disk_pages_total != pages_total:
        warnings.append(
            f"WARNING: wiki page count drift: {disk_pages_total} markdown page(s) on disk, "
            f"but {pages_total} page row(s) in state.db. Run `mdwiki rebuild --pages`."
        )
    if embeddable_pages > 0 and pages_with_embeddings < embeddable_pages:
        warnings.append(
            f"WARNING: {embeddable_pages - pages_with_embeddings} page row(s) have no embedding. Run `mdwiki rebuild --pages` before ingesting."
        )
    return warnings

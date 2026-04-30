"""Read-only summary of wiki state — pure data + a formatter for the CLI.

Counts come straight from ``state.db``; no walking, no LLM, no filesystem cost
beyond the sqlite query.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from mdwiki.discover import WIKI_DIR_NAME
from mdwiki.state import connect


@dataclass(frozen=True)
class EventRow:
    """One row from the ``events`` table — read-only view for status display."""

    ts: float
    kind: str
    summary: str | None


@dataclass(frozen=True)
class StatusReport:
    """A snapshot of wiki state suitable for ``mdwiki status``."""

    pending: int
    ingested: int
    pages_total: int
    pages_by_kind: dict[str, int] = field(default_factory=dict)
    events_total: int = 0
    last_lint_ts: float | None = None
    recent_events: tuple[EventRow, ...] = ()


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

        pages_total = conn.execute("SELECT COUNT(*) AS c FROM pages").fetchone()["c"]
        kind_rows = conn.execute("SELECT kind, COUNT(*) AS c FROM pages GROUP BY kind").fetchall()
        pages_by_kind = {row["kind"]: row["c"] for row in kind_rows}

        events_total = conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]
        lint_row = conn.execute("SELECT MAX(ts) AS ts FROM events WHERE kind = 'lint'").fetchone()
        last_lint_ts = lint_row["ts"] if lint_row and lint_row["ts"] is not None else None

        recent_rows = conn.execute(
            "SELECT ts, kind, summary FROM events ORDER BY ts DESC LIMIT ?",
            (recent_event_limit,),
        ).fetchall()
        recent_events = tuple(EventRow(ts=r["ts"], kind=r["kind"], summary=r["summary"]) for r in recent_rows)

    return StatusReport(
        pending=pending,
        ingested=ingested,
        pages_total=pages_total,
        pages_by_kind=pages_by_kind,
        events_total=events_total,
        last_lint_ts=last_lint_ts,
        recent_events=recent_events,
    )


def format_status(report: StatusReport, *, wiki_root: Path) -> str:
    """Render ``StatusReport`` for human consumption (used by the CLI)."""
    lines: list[str] = [f"mdwiki at {wiki_root}", ""]

    lines.append(f"Sources:    {report.pending} pending, {report.ingested} ingested")

    kind_str = ", ".join(f"{k}: {v}" for k, v in sorted(report.pages_by_kind.items())) or "—"
    lines.append(f"Wiki pages: {report.pages_total}  ({kind_str})")

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

    return "\n".join(lines)


def _fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d %H:%M:%SZ")

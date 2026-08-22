"""Wiki health checks — ``mdwiki lint``.

Four deterministic rules ship in v1.0.0:

- **broken-ref**  — parsed CommonMark link whose local target doesn't exist
- **orphan**      — page that no other wiki page links to
- **stale**       — page whose underlying source was modified after the page was last touched
- **coverage-gap** — source marked `ingested` but with zero ``backrefs`` rows

Contradiction detection, header-focus scoring, and ``--fix`` (interactive
remediation) are deferred to v1.1.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from mdwiki.cross_refs import resolve_page_link_target
from mdwiki.discover import WIKI_DIR_NAME
from mdwiki.markdown import markdown_inline_links, markdown_links
from mdwiki.semantic_pages import INFRASTRUCTURE_PAGE_PATHS, is_semantic_page_path
from mdwiki.state import connect

# Compatibility alias for callers that imported the old lint-local constant.
INFRASTRUCTURE_PAGES = INFRASTRUCTURE_PAGE_PATHS


@dataclass(frozen=True)
class LintFinding:
    """One issue surfaced by ``lint_wiki``.

    Parameters
    ----------
    kind, page_path, message, severity
        Stable fields used by every rule.
    link_text, link_target, link_source, link_source_text
        Populated by ``broken-ref`` findings so ``lint_fix`` can repair the link
        without re-parsing or reconstructing Markdown source. ``None`` for other
        rules.
    """

    kind: str
    page_path: str
    message: str
    severity: str
    link_text: str | None = None
    link_target: str | None = None
    link_source: str | None = None
    link_source_text: str | None = None


@dataclass(frozen=True)
class LintReport:
    """Summary returned by ``lint_wiki``."""

    findings: tuple[LintFinding, ...]
    findings_by_kind: dict[str, int]


def lint_wiki(wiki_root: Path) -> LintReport:
    """Run every lint rule against the wiki at ``wiki_root`` and return findings.

    Records a single ``events`` row of kind ``"lint"`` so ``status`` reports
    when the wiki was last linted.
    """
    findings: list[LintFinding] = []
    findings.extend(_check_broken_refs(wiki_root))
    findings.extend(_check_orphans(wiki_root))
    findings.extend(_check_stale_pages(wiki_root))
    findings.extend(_check_coverage_gaps(wiki_root))
    findings.extend(_check_unverified_quotes(wiki_root))

    _record_lint_event(wiki_root, findings_count=len(findings))

    by_kind = dict(Counter(f.kind for f in findings))
    return LintReport(findings=tuple(findings), findings_by_kind=by_kind)


def _iter_wiki_pages(wiki_root: Path) -> list[Path]:
    """Every regular page under ``wiki/`` (excludes index.md and log.md)."""
    wiki_dir = wiki_root / "wiki"
    if not wiki_dir.is_dir():
        return []
    return [p for p in sorted(wiki_dir.rglob("*.md")) if is_semantic_page_path(p.relative_to(wiki_root).as_posix())]


def _check_broken_refs(wiki_root: Path) -> list[LintFinding]:
    findings: list[LintFinding] = []
    for page in _iter_wiki_pages(wiki_root):
        rel_page = page.relative_to(wiki_root).as_posix()
        for link in markdown_links(page.read_text()):
            resolved_target = resolve_page_link_target(from_page=rel_page, raw_target=link.target)
            if resolved_target is None:
                continue
            if not is_semantic_page_path(resolved_target):
                continue
            resolved = (wiki_root / resolved_target).resolve()
            if not resolved.is_file():
                findings.append(
                    LintFinding(
                        kind="broken-ref",
                        page_path=rel_page,
                        message=f"link [{link.text}]({link.target}) → not found at {resolved}",
                        severity="warn",
                        link_text=link.text,
                        link_target=link.target,
                        link_source=link.source_markup,
                        link_source_text=link.source_text,
                    )
                )
    return findings


def _check_orphans(wiki_root: Path) -> list[LintFinding]:
    """Pages no other wiki page links to."""
    pages = _iter_wiki_pages(wiki_root)
    rel_paths = {p.relative_to(wiki_root).as_posix() for p in pages}
    inbound: set[str] = set()
    for page in pages:
        rel_page = page.relative_to(wiki_root).as_posix()
        for _, target in markdown_inline_links(page.read_text()):
            resolved_target = resolve_page_link_target(from_page=rel_page, raw_target=target)
            if resolved_target is None or not is_semantic_page_path(resolved_target):
                continue
            if resolved_target == rel_page:
                continue
            resolved = (wiki_root / resolved_target).resolve()
            try:
                rel = resolved.relative_to(wiki_root.resolve()).as_posix()
            except ValueError:
                continue
            inbound.add(rel)
    return [
        LintFinding(
            kind="orphan",
            page_path=rel,
            message="no other wiki page links to this page",
            severity="info",
        )
        for rel in sorted(rel_paths - inbound)
        # Syntheses are cross-cutting writeups; concept/entity pages typically don't
        # link back to them, so an orphan finding on a synthesis is noise.
        if not rel.startswith("wiki/syntheses/")
    ]


def _check_stale_pages(wiki_root: Path) -> list[LintFinding]:
    """A page is stale if any source it cites has mtime > page.last_touched_at."""
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    findings: list[LintFinding] = []
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT p.path AS page_path, p.last_touched_at AS page_ts, MAX(s.mtime) AS source_mtime "
            "FROM pages p "
            "JOIN backrefs b ON b.page_path = p.path "
            "JOIN sources s ON s.id = b.source_id "
            "GROUP BY p.path, p.last_touched_at "
            "HAVING source_mtime > page_ts"
        ).fetchall()
    for row in rows:
        findings.append(
            LintFinding(
                kind="stale",
                page_path=row["page_path"],
                message=f"source modified after page (page touched {row['page_ts']:.0f}, source {row['source_mtime']:.0f})",
                severity="info",
            )
        )
    return findings


_UNVERIFIED_QUOTE_MARKER: str = "[unverified-quote]"


def _check_unverified_quotes(wiki_root: Path) -> list[LintFinding]:
    """Pages containing the ``[unverified-quote]`` marker.

    The marker is inserted by ingest in lenient quote-anchor mode (an opt-in
    config flag, defaults off): when a claim's quote can't be verbatim-anchored
    in the source, the claim survives in the page with this marker rather than
    rejecting the entire plan. The lint check surfaces these so the user can
    remove or replace the unverified content. Borrowed from OmegaWiki's
    ``UNCONFIRMED_`` BibTeX-key fail-closed pattern (see research doc).
    """
    findings: list[LintFinding] = []
    for page in _iter_wiki_pages(wiki_root):
        text = page.read_text()
        if _UNVERIFIED_QUOTE_MARKER not in text:
            continue
        rel = page.relative_to(wiki_root).as_posix()
        # Count occurrences for the message; finer-grained line-level info
        # is left to a future ``mdwiki lint --fix`` interactive flow.
        occurrences = text.count(_UNVERIFIED_QUOTE_MARKER)
        findings.append(
            LintFinding(
                kind="unverified-quote",
                page_path=rel,
                message=f"contains {occurrences} unverified-quote marker(s); review or remove",
                severity="warn",
            )
        )
    return findings


def _check_coverage_gaps(wiki_root: Path) -> list[LintFinding]:
    """Sources marked ingested but contributing zero backrefs."""
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT s.id, s.original_path FROM sources s "
            "LEFT JOIN backrefs b ON b.source_id = s.id "
            "WHERE s.status = 'ingested' "
            "GROUP BY s.id, s.original_path "
            "HAVING COUNT(b.rowid) = 0"
        ).fetchall()
    return [
        LintFinding(
            kind="coverage-gap",
            page_path=row["original_path"],
            message=f"source {row['id']} ({row['original_path']}) is ingested but produced 0 backrefs",
            severity="info",
        )
        for row in rows
    ]


def _record_lint_event(wiki_root: Path, *, findings_count: int) -> None:
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    summary = f"lint: {findings_count} finding(s)"
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO events (ts, kind, summary) VALUES (?, ?, ?)",
            (time.time(), "lint", summary),
        )
        conn.commit()

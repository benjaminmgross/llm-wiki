"""Interactive remediation of lint findings.

Default mode handles ONLY deterministic fixes:
- ``broken-ref`` → strip one uniquely identifiable broken CommonMark link and
  keep its source label as bare prose. Ambiguous source forms fail closed.

``mode="full"`` adds LLM-touching fixes:
- ``stale`` → re-ingest the source via ``ingest_source(force=True)``.
- ``coverage-gap`` → re-ingest with the same force semantics (the LLM gets a fresh
  shot at producing backrefs that didn't materialize the first time).

Orphans are always left alone — fixing them requires human judgment about WHERE the
orphan should be cross-referenced from. ``mdwiki query --file`` is the right
mechanism for promoting an orphan into the cross-ref graph.

Each successful fix is wrapped in an ``IngestTransaction`` so it's reversible via
``mdwiki undo``.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from mdwiki.discover import WIKI_DIR_NAME
from mdwiki.ingest import IngestResult, ingest_source
from mdwiki.lint import LintFinding, lint_wiki, order_findings
from mdwiki.state import connect
from mdwiki.transaction import IngestTransaction

logger = logging.getLogger(__name__)

LintFixMode = Literal["default", "full"]


@dataclass(frozen=True)
class LintFixResult:
    """Outcome of a ``lint_fix`` call.

    Parameters
    ----------
    fixed_count : int
        Findings successfully remediated.
    skipped_count : int
        Findings the user declined or the mode skipped.
    failed_count : int
        Findings whose fix raised an error (write failed, ingest_source failed, etc.).
    """

    fixed_count: int
    skipped_count: int
    failed_count: int


def lint_fix(
    wiki_root: Path,
    *,
    mode: LintFixMode = "default",
    yes: bool = False,
    confirm: Callable[[LintFinding], bool] | None = None,
    max_consecutive_failures: int | None = 5,
    select: frozenset[int] | None = None,
) -> LintFixResult:
    """Walk lint findings and apply per-kind fixes interactively.

    Parameters
    ----------
    wiki_root : Path
        Folder containing ``.mdwiki/``.
    mode : "default" | "full"
        ``"default"``: only deterministic fixes (broken-ref). ``"full"``: also
        re-ingests stale and coverage-gap findings (LLM round-trips).
    yes : bool, optional
        Skip the per-finding prompt — apply every applicable fix.
    confirm : callable, optional
        Called with each ``LintFinding`` when ``yes`` is False; return True to apply.
        Default is a terminal y/N prompt.
    max_consecutive_failures : int, optional
        Stop after this many applicable fixes fail in a row. This prevents
        ``--fix=full --yes`` from spending through a large backlog when every
        LLM-backed re-ingest is failing for the same systemic reason. ``None``
        disables the circuit breaker.
    select : frozenset[int], optional
        1-based finding numbers from the severity-ordered report
        (``lint.order_findings``). Only those findings are attempted, and an
        explicit selection may include provider-backed fixes regardless of
        ``mode`` because the user named them.
    """
    chooser = confirm or _terminal_confirm
    findings = order_findings(lint_wiki(wiki_root).findings)
    if select is not None:
        mode = "full"

    fixed = 0
    skipped = 0
    failed = 0
    consecutive_failures = 0
    for index, finding in enumerate(findings):
        if select is not None and (index + 1) not in select:
            skipped += 1
            consecutive_failures = 0
            continue
        if not _is_applicable(finding, mode=mode):
            skipped += 1
            consecutive_failures = 0
            continue
        if not yes and not chooser(finding):
            skipped += 1
            consecutive_failures = 0
            continue
        try:
            handled = _dispatch(finding, wiki_root=wiki_root, mode=mode)
        except Exception as exc:  # noqa: BLE001 — one bad fix shouldn't abort the rest
            print(
                f"  ! lint-fix failed for {finding.kind} on {finding.page_path}: {exc}",
                file=sys.stderr,
            )
            logger.exception("lint-fix failed for %s on %s", finding.kind, finding.page_path)
            failed += 1
            consecutive_failures += 1
            if max_consecutive_failures is not None and consecutive_failures >= max_consecutive_failures:
                remaining_findings = findings[index + 1 :]
                remaining_applicable = sum(1 for remaining in remaining_findings if _is_applicable(remaining, mode=mode))
                skipped += len(remaining_findings)
                print(
                    f"  ! stopping lint-fix after {consecutive_failures} consecutive failures; "
                    f"skipping {remaining_applicable} remaining applicable finding(s).",
                    file=sys.stderr,
                )
                break
            continue
        if handled:
            fixed += 1
            consecutive_failures = 0
        else:
            skipped += 1
            consecutive_failures = 0

    return LintFixResult(fixed_count=fixed, skipped_count=skipped, failed_count=failed)


def _is_applicable(finding: LintFinding, *, mode: LintFixMode) -> bool:
    """Whether ``mode`` knows how to fix this finding kind."""
    if finding.kind == "broken-ref":
        return True
    if mode == "full" and finding.kind in ("stale", "coverage-gap"):
        return True
    return False


def _dispatch(finding: LintFinding, *, wiki_root: Path, mode: LintFixMode) -> bool:
    """Route a finding to its fix. Returns True if a change was applied."""
    if finding.kind == "broken-ref":
        return _fix_broken_ref(finding, wiki_root=wiki_root)
    if mode == "full" and finding.kind in ("stale", "coverage-gap"):
        return _fix_re_ingest(finding, wiki_root=wiki_root)
    return False


# Fallback parser for findings produced by older lint runs that didn't populate
# ``link_text`` / ``link_target``. New findings should always carry those fields.
_BROKEN_REF_TARGET_RE = re.compile(r"link \[([^\]]+)\]\(([^)]+)\) → not found")


def _fix_broken_ref(finding: LintFinding, *, wiki_root: Path) -> bool:
    """Replace ``[text](broken-target)`` with bare ``text`` in the linking page.

    Prefers parser-derived source forms populated by ``_check_broken_refs``.
    Falls back to structured semantic fields and then the human-readable
    message only for findings created by older versions or hand-built fixtures.
    """
    if finding.link_source is not None and finding.link_source_text is not None and finding.link_target is not None:
        needle = finding.link_source
        replacement = finding.link_source_text
        target = finding.link_target
    elif finding.link_text is not None and finding.link_target is not None:
        link_text = finding.link_text
        target = finding.link_target
        needle = f"[{link_text}]({target})"
        replacement = link_text
    else:
        match = _BROKEN_REF_TARGET_RE.search(finding.message)
        if match is None:
            return False
        link_text = match.group(1)
        target = match.group(2)
        needle = f"[{link_text}]({target})"
        replacement = link_text

    page_path = wiki_root / finding.page_path
    if not page_path.is_file():
        return False
    with page_path.open(encoding="utf-8", newline="") as page_file:
        original = page_file.read()
    # Parser tokens supply the exact source form but not an absolute character
    # offset. Fail closed when that form is ambiguous (for example, duplicated
    # inside a code span or fence) rather than rewriting unparsed examples.
    if original.count(needle) != 1:
        return False
    rewritten = original.replace(needle, replacement, 1)

    summary = f"lint-fix broken-ref in {finding.page_path}: drop link to {target}"
    with IngestTransaction(wiki_root=wiki_root, source_id=None, summary=summary) as tx:
        tx.write_file(page_path, rewritten)
    return True


def _fix_re_ingest(finding: LintFinding, *, wiki_root: Path) -> bool:
    """Re-run ingest for the source feeding this finding (force=True)."""
    source_id = _resolve_source_id(finding, wiki_root=wiki_root)
    if source_id is None:
        return False
    result = ingest_source(wiki_root, source_id, yes=True, force=True)
    return isinstance(result, IngestResult) and result.applied


def _resolve_source_id(finding: LintFinding, *, wiki_root: Path) -> str | None:
    """Find which source feeds the page named in a stale/coverage-gap finding."""
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    with connect(db_path) as conn:
        if finding.kind == "stale":
            row = conn.execute(
                "SELECT b.source_id, MAX(s.mtime) AS m "
                "FROM backrefs b JOIN sources s ON s.id = b.source_id "
                "WHERE b.page_path = ? "
                "GROUP BY b.source_id "
                "ORDER BY m DESC LIMIT 1",
                (finding.page_path,),
            ).fetchone()
        else:
            # coverage-gap finding's page_path holds the original_path of the source
            row = conn.execute(
                "SELECT id AS source_id FROM sources WHERE original_path = ? LIMIT 1",
                (finding.page_path,),
            ).fetchone()
    return row["source_id"] if row else None


def _terminal_confirm(finding: LintFinding) -> bool:
    print(f"\n[{finding.kind}] {finding.page_path}")
    print(f"  {finding.message}")
    answer = input("  Apply fix? [y/N] ").strip().lower()
    return answer in ("y", "yes")

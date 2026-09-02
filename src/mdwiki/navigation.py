"""Generated navigation pages: ``wiki/index.md`` and ``wiki/concept-table.md``.

Both are regenerated inside every transaction that writes semantic pages.
``build_index`` (``index.py``) renders the kind-grouped catalog; this module
adds the per-page concept table (sources, related pages, status) built from
``state.db`` with no model call, and the single ``write_navigation`` entry
point every writer uses so the two files never drift apart.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from mdwiki.cross_refs import resolve_page_link_target
from mdwiki.discover import WIKI_DIR_NAME
from mdwiki.index import _extract_title, build_index
from mdwiki.markdown import markdown_inline_links
from mdwiki.page_kinds import page_kind_folders_for_wiki
from mdwiki.semantic_pages import is_semantic_page_path
from mdwiki.state import connect

if TYPE_CHECKING:
    from mdwiki.transaction import IngestTransaction

CONCEPT_TABLE_PATH: str = "wiki/concept-table.md"
INDEX_PATH: str = "wiki/index.md"
_MAX_RELATED_SHOWN: int = 4


@dataclass(frozen=True)
class ConceptRow:
    """One row of the concept table."""

    path: str
    title: str
    kind: str
    source_count: int
    related: tuple[str, ...]
    status: str
    updated: str

    def render(self) -> str:
        link = self.path.removeprefix("wiki/")
        related = ", ".join(self.related[:_MAX_RELATED_SHOWN])
        if len(self.related) > _MAX_RELATED_SHOWN:
            related += f" (+{len(self.related) - _MAX_RELATED_SHOWN})"
        return f"| [{self.title}]({link}) | {self.kind} | {self.source_count} | {related or '—'} | {self.status} | {self.updated} |"


def write_navigation(tx: IngestTransaction, wiki_root: Path, *, conn: sqlite3.Connection | None = None) -> None:
    """Regenerate ``index.md`` and ``concept-table.md`` through the open transaction.

    ``IngestTransaction`` calls this itself at commit time whenever a semantic
    page was written, passing its own connection so uncommitted ``pages``,
    ``backrefs``, and ``contradictions`` rows are reflected.
    """
    tx.write_file(wiki_root / INDEX_PATH, build_index(wiki_root))
    tx.write_file(wiki_root / CONCEPT_TABLE_PATH, build_concept_table(wiki_root, conn=conn))


def regenerate_navigation(wiki_root: Path) -> None:
    """Write both navigation files directly (outside a transaction; used by rebuild paths)."""
    (wiki_root / "wiki").mkdir(exist_ok=True)
    (wiki_root / INDEX_PATH).write_text(build_index(wiki_root))
    (wiki_root / CONCEPT_TABLE_PATH).write_text(build_concept_table(wiki_root))


def build_concept_table(wiki_root: Path, *, conn: sqlite3.Connection | None = None) -> str:
    """Return the Markdown body of ``wiki/concept-table.md`` for the current state."""
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    rows = concept_rows(wiki_root, conn=conn)
    lines = [
        "# Concept table",
        "",
        f"_Last updated: {today}_",
        "",
        "One row per page, generated from `state.db` on every transaction. Status: `unsourced` (no cited source), `single-source`, `multi-source`, or `contradicted` (an unresolved contradiction is recorded on the page).",
        "",
        "| Page | Kind | Sources | Related | Status | Updated |",
        "|---|---|---|---|---|---|",
    ]
    if rows:
        lines.extend(row.render() for row in rows)
    else:
        lines.append("| (no pages yet) | | | | | |")
    return "\n".join(lines).rstrip() + "\n"


def concept_rows(wiki_root: Path, *, conn: sqlite3.Connection | None = None) -> list[ConceptRow]:
    """Rows in kind order (profile baseline first), then path."""
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    if conn is None and not db_path.is_file():
        return []
    if conn is None:
        with connect(db_path) as own_conn:
            page_rows, source_counts, contradicted = _read_rows(own_conn)
    else:
        page_rows, source_counts, contradicted = _read_rows(conn)

    kind_order = {kind: index for index, (kind, _heading, _folder) in enumerate(page_kind_folders_for_wiki(wiki_root))}
    titles: dict[str, str] = {}
    contents: dict[str, str] = {}
    for row in page_rows:
        full = wiki_root / row["path"]
        if row["kind"] == "source" or not full.is_file() or not is_semantic_page_path(row["path"]):
            continue
        contents[row["path"]] = full.read_text()
        titles[row["path"]] = _extract_title(full)

    related: dict[str, list[str]] = defaultdict(list)
    for path, content in contents.items():
        for _text, raw_target in markdown_inline_links(content):
            target = resolve_page_link_target(from_page=path, raw_target=raw_target)
            if target is None or target == path or not is_semantic_page_path(target):
                continue
            title = titles.get(target) or _stem_title(target)
            if title not in related[path]:
                related[path].append(title)

    rows: list[ConceptRow] = []
    for row in page_rows:
        path = row["path"]
        if path not in contents:
            continue
        count = int(source_counts.get(path, 0))
        if path in contradicted:
            status = "contradicted"
        elif count == 0:
            status = "unsourced"
        elif count == 1:
            status = "single-source"
        else:
            status = "multi-source"
        rows.append(
            ConceptRow(
                path=path,
                title=titles[path],
                kind=row["kind"],
                source_count=count,
                related=tuple(related.get(path, [])),
                status=status,
                updated=datetime.fromtimestamp(float(row["last_touched_at"]), tz=UTC).strftime("%Y-%m-%d"),
            )
        )
    rows.sort(key=lambda item: (kind_order.get(item.kind, len(kind_order)), item.path))
    return rows


def _read_rows(conn: sqlite3.Connection) -> tuple[list[sqlite3.Row], dict[str, int], set[str]]:
    page_rows = conn.execute("SELECT path, kind, last_touched_at FROM pages").fetchall()
    source_counts = {
        row["page_path"]: int(row["c"])
        for row in conn.execute("SELECT page_path, COUNT(DISTINCT source_id) AS c FROM backrefs GROUP BY page_path").fetchall()
    }
    contradicted = {
        row["page_path"] for row in conn.execute("SELECT DISTINCT page_path FROM contradictions WHERE resolution = 'pending'").fetchall()
    }
    return page_rows, source_counts, contradicted


def _stem_title(path: str) -> str:
    return Path(path).stem.replace("-", " ").title()

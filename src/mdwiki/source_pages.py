"""Generated ``wiki/sources/<id>-<slug>.md`` pages — provenance in page form.

One page per ingested source, rebuilt from the applied plan on every ingest of
that source: verdict, rationale, the pages it fed, every anchored quote, and
any contradictions it recorded. ``mdwiki source <id>`` prints the same facts
from the database; this file makes them visible in Obsidian and Dataview and
gives lint's ``orphan`` rule a page kind to exempt.

Because the page lists every anchored quote with its target page and section,
``mdwiki rebuild --pages`` can restore ``backrefs`` (and, from the pages'
managed blocks, ``contradictions``) after ``state.db`` is lost — the one
recovery gap ``rebuild`` documented since v1.0.
"""

from __future__ import annotations

import posixpath
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from mdwiki.frontmatter import apply_page_metadata, read_frontmatter
from mdwiki.plan import Plan

SOURCE_KIND: str = "source"
SOURCES_FOLDER: str = "sources"
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def source_page_path(*, source_id: str, original_path: str) -> str:
    """Return the wiki-relative path of the generated page for one source."""
    slug = _SLUG_RE.sub("-", Path(original_path).stem.lower()).strip("-")[:50] or "untitled"
    return f"wiki/{SOURCES_FOLDER}/{source_id}-{slug}.md"


def build_source_page(*, source_id: str, original_path: str, raw_path: str, plan: Plan, today: str | None = None) -> str:
    """Render the source page body (frontmatter included) for ``plan`` as applied."""
    page_path = source_page_path(source_id=source_id, original_path=original_path)
    lines: list[str] = [f"# {original_path}", ""]
    lines.append(f"- Source id: `{source_id}`")
    lines.append(f"- Raw copy: `{raw_path}`")
    lines.append(f"- Verdict: {plan.verdict}")
    lines.append("")
    lines.append("## Rationale")
    lines.append("")
    lines.append(plan.rationale.strip() or "(none)")
    lines.append("")

    touched: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for new_page in plan.new_pages:
        for claim in new_page.claims:
            touched[new_page.path].append((claim.source_section_id, claim.quote))
    for update in plan.updates:
        for claim in update.claims:
            touched[update.page].append((claim.source_section_id, claim.quote))
    for contradiction in plan.contradictions:
        for claim in contradiction.claims:
            touched[contradiction.page].append((claim.source_section_id, claim.quote))

    lines.append("## Pages fed")
    lines.append("")
    if touched:
        for target in sorted(touched):
            lines.append(f"- [{_title_for(target)}]({_relative_link(page_path, target)})")
    else:
        lines.append("(none — this source produced no page changes)")
    lines.append("")

    lines.append("## Anchored quotes")
    lines.append("")
    if touched:
        for target in sorted(touched):
            lines.append(f"### [{_title_for(target)}]({_relative_link(page_path, target)})")
            lines.append("")
            for section_id, quote in touched[target]:
                lines.append(f'- `{section_id}`: "{quote}"')
            lines.append("")
    else:
        lines.append("(none)")
        lines.append("")

    if plan.contradictions:
        lines.append("## Contradictions recorded")
        lines.append("")
        for entry in plan.contradictions:
            lines.append(
                f"- [{entry.resolution}] [{_title_for(entry.page)}]({_relative_link(page_path, entry.page)}): "
                f'wiki says "{entry.existing_claim}" | source says "{entry.source_claim}"'
            )
        lines.append("")

    body = "\n".join(lines).rstrip() + "\n"
    return apply_page_metadata(body, path=page_path, kind=SOURCE_KIND, source_ids=(source_id,), today=today)


def _title_for(page_path: str) -> str:
    return Path(page_path).stem.replace("-", " ").title()


def _relative_link(from_page: str, to_page: str) -> str:
    return posixpath.relpath(to_page, posixpath.dirname(from_page))


@dataclass(frozen=True)
class RestoredProvenance:
    """Counts from ``restore_provenance``."""

    backrefs: int
    contradictions: int


_ANCHOR_HEADING_RE = re.compile(r"^### \[[^\]]*\]\(([^)]+)\)\s*$")
_ANCHOR_LINE_RE = re.compile(r"^- `(?P<section>[^`]+)`: \"(?P<quote>.*)\"\s*$")
_SOURCE_ID_RE = re.compile(r"^- Source id: `(?P<id>[^`]+)`\s*$", re.MULTILINE)


def parse_source_page(text: str, *, page_path: str) -> tuple[str | None, list[tuple[str, str, str]]]:
    """Return ``(source_id, [(target_page, section_id, quote), ...])`` from a generated source page."""
    _meta, body = read_frontmatter(text)
    id_match = _SOURCE_ID_RE.search(body)
    source_id = id_match.group("id") if id_match else None
    anchors: list[tuple[str, str, str]] = []
    current_target: str | None = None
    in_section = False
    for line in body.splitlines():
        if line.startswith("## "):
            in_section = line.strip() == "## Anchored quotes"
            current_target = None
            continue
        if not in_section:
            continue
        heading = _ANCHOR_HEADING_RE.match(line)
        if heading:
            current_target = posixpath.normpath(posixpath.join(posixpath.dirname(page_path), heading.group(1)))
            continue
        anchor = _ANCHOR_LINE_RE.match(line)
        if anchor and current_target:
            anchors.append((current_target, anchor.group("section"), anchor.group("quote")))
    return source_id, anchors


def restore_provenance(conn: sqlite3.Connection, *, wiki_root: Path) -> RestoredProvenance:
    """Re-derive missing ``backrefs`` and ``contradictions`` rows from pages on disk.

    Only rows that do not already exist are inserted, so repeated rebuilds
    are idempotent and a live wiki is never duplicated. Rows are inserted
    only when both the source and the target page exist in ``state.db``.
    """
    from mdwiki.contradictions import parse_block_entries

    known_sources = {row["id"] for row in conn.execute("SELECT id FROM sources").fetchall()}
    known_pages = {row["path"] for row in conn.execute("SELECT path FROM pages").fetchall()}
    backrefs = 0
    sources_dir = wiki_root / "wiki" / SOURCES_FOLDER
    if sources_dir.is_dir():
        for page in sorted(sources_dir.glob("*.md")):
            rel = page.relative_to(wiki_root).as_posix()
            source_id, anchors = parse_source_page(page.read_text(), page_path=rel)
            if source_id is None or source_id not in known_sources:
                continue
            for target, section_id, quote in anchors:
                if target not in known_pages:
                    continue
                exists = conn.execute(
                    "SELECT 1 FROM backrefs WHERE page_path = ? AND source_id = ? AND section_anchor IS ? AND quote IS ?",
                    (target, source_id, section_id, quote),
                ).fetchone()
                if exists is None:
                    conn.execute(
                        "INSERT INTO backrefs (page_path, source_id, section_anchor, quote) VALUES (?, ?, ?, ?)",
                        (target, source_id, section_id, quote),
                    )
                    backrefs += 1

    contradictions = 0
    for page_path in sorted(known_pages):
        full = wiki_root / page_path
        if not full.is_file():
            continue
        for entry in parse_block_entries(full.read_text()):
            exists = conn.execute(
                "SELECT 1 FROM contradictions WHERE page_path = ? AND existing_claim = ? AND source_claim = ?",
                (page_path, entry.existing_claim, entry.source_claim),
            ).fetchone()
            if exists is not None:
                continue
            source_id = entry.via if entry.via in known_sources else None
            ts_row = conn.execute("SELECT last_touched_at FROM pages WHERE path = ?", (page_path,)).fetchone()
            conn.execute(
                "INSERT INTO contradictions (page_path, source_id, existing_claim, source_claim, resolution, ts) VALUES (?, ?, ?, ?, ?, ?)",
                (page_path, source_id, entry.existing_claim, entry.source_claim, entry.resolution, float(ts_row[0]) if ts_row else 0.0),
            )
            contradictions += 1
    return RestoredProvenance(backrefs=backrefs, contradictions=contradictions)

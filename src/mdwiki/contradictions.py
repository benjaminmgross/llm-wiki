"""Materialize plan contradictions as a managed block in the page that holds the disputed claim.

Mirrors ``cross_refs.py``: the block is owned by mdwiki, delimited by HTML
comment markers that ``markdown-it-py`` recognizes as ``html_block`` tokens
(so marker-looking text inside code fences is never treated as a boundary),
and regenerated on every apply from the union of the block's existing entries
and the plan's new ones. ``state.db``'s ``contradictions`` table is the
queryable twin that lint and status read; the block is the human-visible one.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from mdwiki.markdown import parse_commonmark
from mdwiki.plan import Contradiction, Plan

CONTRADICTIONS_BLOCK_START: str = "<!-- mdwiki:contradictions -->"
CONTRADICTIONS_BLOCK_END: str = "<!-- /mdwiki:contradictions -->"

_ENTRY_RE = re.compile(r"^- \[(?P<resolution>[a-z-]+)\] wiki: (?P<existing>.*?) \| source: (?P<source>.*?)(?: \| via: (?P<via>\S+))?$")


class ContradictionMaterializationError(ValueError):
    """Raised when a contradiction names a page that does not exist after planned writes."""


@dataclass(frozen=True)
class ContradictionEntry:
    """One line of a managed contradictions block."""

    existing_claim: str
    source_claim: str
    resolution: str
    via: str | None

    def render(self) -> str:
        via = f" | via: {self.via}" if self.via else ""
        return f"- [{self.resolution}] wiki: {_single_line(self.existing_claim)} | source: {_single_line(self.source_claim)}{via}"


def materialize_contradictions(*, wiki_root: Path, plan: Plan, page_bodies: dict[str, str], source_id: str | None) -> dict[str, str]:
    """Return ``page_bodies`` extended with a regenerated contradictions block on every affected page.

    Pages the plan does not otherwise write are read from disk (read-modify-write),
    exactly like cross-reference source pages.
    """
    grouped: dict[str, list[Contradiction]] = defaultdict(list)
    for entry in plan.contradictions:
        if entry.page not in page_bodies and not (wiki_root / entry.page).is_file():
            raise ContradictionMaterializationError(f"Contradiction target {entry.page} does not exist after planned page writes.")
        grouped[entry.page].append(entry)

    bodies = dict(page_bodies)
    for page in sorted(grouped):
        current = bodies.get(page)
        if current is None:
            current = (wiki_root / page).read_text()
        bodies[page] = materialize_page_contradictions(current, new_entries=grouped[page], source_id=source_id)
    return bodies


def materialize_page_contradictions(content: str, *, new_entries: list[Contradiction], source_id: str | None) -> str:
    """Merge ``new_entries`` into the page's managed block (newest resolution wins for a repeated pair)."""
    ranges = managed_block_ranges(content)
    existing = parse_block_entries(content, ranges=ranges)
    ordinary = _without_ranges(content, ranges=ranges)

    merged: dict[tuple[str, str], ContradictionEntry] = {}
    for recorded in existing:
        merged[(recorded.existing_claim, recorded.source_claim)] = recorded
    for planned in new_entries:
        key = (_single_line(planned.existing_claim), _single_line(planned.source_claim))
        merged[key] = ContradictionEntry(existing_claim=key[0], source_claim=key[1], resolution=planned.resolution, via=source_id)

    if not merged:
        return ordinary if ranges else content
    lines = "\n".join(entry.render() for entry in merged.values())
    block = f"{CONTRADICTIONS_BLOCK_START}\n## Contradictions\n\n{lines}\n{CONTRADICTIONS_BLOCK_END}"
    if not ordinary or ordinary.endswith("\n\n"):
        separator = ""
    elif ordinary.endswith("\n"):
        separator = "\n"
    else:
        separator = "\n\n"
    return f"{ordinary}{separator}{block}\n"


def parse_block_entries(content: str, *, ranges: list[tuple[int, int]] | None = None) -> list[ContradictionEntry]:
    """Return the entries recorded in the page's managed block, in order."""
    if ranges is None:
        ranges = managed_block_ranges(content)
    entries: list[ContradictionEntry] = []
    for start, end in ranges:
        for line in content[start:end].splitlines():
            match = _ENTRY_RE.match(line.rstrip("\r"))
            if match is None:
                continue
            entries.append(
                ContradictionEntry(
                    existing_claim=match.group("existing"),
                    source_claim=match.group("source"),
                    resolution=match.group("resolution"),
                    via=match.group("via"),
                )
            )
    return entries


def managed_block_ranges(content: str) -> list[tuple[int, int]]:
    """Character ranges of paired contradictions markers, found via markdown-it html_block tokens."""
    line_offsets = [0]
    for line in content.splitlines(keepends=True):
        line_offsets.append(line_offsets[-1] + len(line))
    ranges: list[tuple[int, int]] = []
    pending_start: int | None = None
    for token in parse_commonmark(content):
        if token.type != "html_block" or token.map is None:
            continue
        start_line, end_line = token.map
        if end_line != start_line + 1:
            continue
        line_start = line_offsets[start_line]
        line_end = line_offsets[end_line]
        marker = content[line_start:line_end].rstrip("\r\n")
        if marker == CONTRADICTIONS_BLOCK_START:
            pending_start = line_start
        elif marker == CONTRADICTIONS_BLOCK_END and pending_start is not None:
            ranges.append((pending_start, line_end))
            pending_start = None
    return ranges


def _without_ranges(content: str, *, ranges: list[tuple[int, int]]) -> str:
    pieces: list[str] = []
    cursor = 0
    for start, end in ranges:
        pieces.append(content[cursor:start])
        cursor = end
    pieces.append(content[cursor:])
    return "".join(pieces)


def _single_line(text: str) -> str:
    return " ".join(text.split())

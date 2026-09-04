"""Deterministically materialize plan cross-references as Markdown links."""

from __future__ import annotations

import posixpath
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote, unquote

from mdwiki.markdown import markdown_inline_links, parse_commonmark
from mdwiki.plan import CrossRef, Plan

_BLOCK_START = "<!-- mdwiki:cross-refs -->"
_BLOCK_END = "<!-- /mdwiki:cross-refs -->"


class CrossRefMaterializationError(ValueError):
    """Raised when a cross-reference cannot be applied to the current wiki."""


def materialize_cross_refs(*, wiki_root: Path, plan: Plan) -> dict[str, str]:
    """Return final page bodies after applying every planned cross-reference."""
    page_bodies = {new_page.path: new_page.content for new_page in plan.new_pages}
    page_bodies.update({update.page: update.content for update in plan.updates})
    update_paths = {update.page for update in plan.updates}
    grouped: dict[str, list[CrossRef]] = defaultdict(list)
    for cross_ref in plan.cross_refs:
        _require_endpoint(wiki_root=wiki_root, page_bodies=page_bodies, path=cross_ref.from_page)
        _require_endpoint(wiki_root=wiki_root, page_bodies=page_bodies, path=cross_ref.to_page)
        grouped[cross_ref.from_page].append(cross_ref)

    for from_page in sorted(set(grouped) | update_paths):
        source_path = wiki_root / from_page
        existing_body = (
            source_path.read_text()
            if source_path.is_file() and (from_page in update_paths or from_page not in page_bodies)
            else None
        )
        source_body = page_bodies.get(from_page)
        if source_body is None:
            source_body = existing_body
        if source_body is None:  # pragma: no cover - guarded by _require_endpoint
            raise CrossRefMaterializationError(f"Cross-reference source {from_page} does not exist after planned page writes.")
        page_bodies[from_page] = _materialize_page(
            source_body,
            from_page=from_page,
            refs=grouped[from_page],
            existing_content=existing_body,
        )
    return page_bodies


def _materialize_page(content: str, *, from_page: str, refs: list[CrossRef], existing_content: str | None = None) -> str:
    owned_ranges = _managed_block_ranges(content)
    ordinary_content = _without_managed_blocks(content, ranges=owned_ranges)
    ordinary_targets = _linked_pages(ordinary_content, from_page=from_page)
    managed: dict[str, str] = {}
    if existing_content is not None:
        existing_ranges = _managed_block_ranges(existing_content)
        managed.update(_managed_targets(existing_content, from_page=from_page, ranges=existing_ranges))
    managed.update(_managed_targets(content, from_page=from_page, ranges=owned_ranges))
    managed = {target: anchor for target, anchor in managed.items() if target not in ordinary_targets}
    chosen: dict[str, str] = {}
    for ref in sorted(refs, key=lambda item: (item.to_page, item.anchor_text)):
        if ref.to_page in ordinary_targets:
            continue
        _ = chosen.setdefault(ref.to_page, ref.anchor_text)
    managed.update(chosen)
    if not managed:
        return ordinary_content if owned_ranges else content
    links = [
        f"- [{_encode_link_text(anchor)}]({encode_page_link_target(from_page=from_page, to_page=target)})"
        for target, anchor in sorted(managed.items())
    ]
    rendered_links = "\n".join(links)
    block = f"{_BLOCK_START}\n## Related\n\n{rendered_links}\n{_BLOCK_END}"
    if not ordinary_content or ordinary_content.endswith("\n\n"):
        separator = ""
    elif ordinary_content.endswith("\n"):
        separator = "\n"
    else:
        separator = "\n\n"
    return f"{ordinary_content}{separator}{block}\n"


def _managed_targets(content: str, *, from_page: str, ranges: list[tuple[int, int]]) -> dict[str, str]:
    targets: dict[str, str] = {}
    for start, end in ranges:
        for anchor, raw_target in markdown_inline_links(content[start:end]):
            resolved = resolve_page_link_target(from_page=from_page, raw_target=raw_target)
            if resolved is None:
                continue
            _ = targets.setdefault(resolved, anchor)
    return targets


def _without_managed_blocks(content: str, *, ranges: list[tuple[int, int]]) -> str:
    pieces: list[str] = []
    cursor = 0
    for start, end in ranges:
        pieces.append(content[cursor:start])
        cursor = end
    pieces.append(content[cursor:])
    return "".join(pieces)


def _managed_block_ranges(content: str) -> list[tuple[int, int]]:
    """Return character ranges for paired mdwiki-owned HTML markers.

    ``markdown-it-py`` identifies block HTML and supplies source line maps, so
    marker-looking text in code fences or inline prose cannot become ownership
    boundaries. A second start marker supersedes an unmatched earlier start.
    """
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
        if marker == _BLOCK_START:
            pending_start = line_start
        elif marker == _BLOCK_END and pending_start is not None:
            ranges.append((pending_start, line_end))
            pending_start = None
    return ranges


def _encode_link_text(anchor: str) -> str:
    return anchor.replace("\\", "\\\\")


def _require_endpoint(*, wiki_root: Path, page_bodies: dict[str, str], path: str) -> None:
    if path not in page_bodies and not (wiki_root / path).is_file():
        raise CrossRefMaterializationError(f"Cross-reference endpoint {path} does not exist after planned page writes.")


def _linked_pages(content: str, *, from_page: str) -> set[str]:
    return {
        target
        for _, raw_target in markdown_inline_links(content)
        if (target := resolve_page_link_target(from_page=from_page, raw_target=raw_target)) is not None
    }


def encode_page_link_target(*, from_page: str, to_page: str) -> str:
    """Return a relative, URI-safe Markdown target for one semantic page."""
    relative = posixpath.relpath(to_page, posixpath.dirname(from_page))
    return quote(relative, safe="/-._~")


def resolve_page_link_target(*, from_page: str, raw_target: str) -> str | None:
    """Resolve a local Markdown target to a canonical wiki path.

    A literal ``#`` denotes a Markdown fragment. Percent-decoding happens
    afterwards, so ``%23`` round-trips as a filename character instead.
    """
    path_part = raw_target.split("#", 1)[0]
    if path_part.startswith(("http://", "https://", "mailto:")):
        return None
    decoded = unquote(path_part)
    return posixpath.normpath(posixpath.join(posixpath.dirname(from_page), decoded))

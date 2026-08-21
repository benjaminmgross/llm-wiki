"""Deterministically materialize plan cross-references as Markdown links."""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote, unquote

from mdwiki.plan import CrossRef, Plan

_LINK_OPEN_RE = re.compile(r"\[([^\]\n]+)\]\(")
_MANAGED_LINK_RE = re.compile(r"^- \[([^\]]+)\]\(([^)]+)\)$", flags=re.MULTILINE)
_BLOCK_START = "<!-- mdwiki:cross-refs -->"
_BLOCK_END = "<!-- /mdwiki:cross-refs -->"
_MANAGED_LINK_LINE = r"- \[[^\]\n]+\]\([^()\n]+\)\n"
_BLOCK_RE = re.compile(rf"(?m)^{re.escape(_BLOCK_START)}\n## Related\n\n(?P<links>(?:{_MANAGED_LINK_LINE})*){re.escape(_BLOCK_END)}$")


class CrossRefMaterializationError(ValueError):
    """Raised when a cross-reference cannot be applied to the current wiki."""


def materialize_cross_refs(*, wiki_root: Path, plan: Plan) -> dict[str, str]:
    """Return final page bodies after applying every planned cross-reference."""
    page_bodies = {new_page.path: new_page.content for new_page in plan.new_pages}
    page_bodies.update({update.page: update.content for update in plan.updates})
    grouped: dict[str, list[CrossRef]] = defaultdict(list)
    for cross_ref in plan.cross_refs:
        _require_endpoint(wiki_root=wiki_root, page_bodies=page_bodies, path=cross_ref.from_page)
        _require_endpoint(wiki_root=wiki_root, page_bodies=page_bodies, path=cross_ref.to_page)
        grouped[cross_ref.from_page].append(cross_ref)

    for from_page in sorted(grouped):
        source_body = page_bodies.get(from_page)
        if source_body is None:
            source_body = (wiki_root / from_page).read_text()
        page_bodies[from_page] = _materialize_page(source_body, from_page=from_page, refs=grouped[from_page])
    return page_bodies


def _materialize_page(content: str, *, from_page: str, refs: list[CrossRef]) -> str:
    ordinary_content = _without_fenced_code(_without_managed_blocks(content))
    managed = {
        target: anchor
        for target, anchor in _managed_targets(content, from_page=from_page).items()
        if not _links_to(ordinary_content, from_page=from_page, to_page=target)
    }
    chosen: dict[str, str] = {}
    for ref in sorted(refs, key=lambda item: (item.to_page, item.anchor_text)):
        if _links_to(ordinary_content, from_page=from_page, to_page=ref.to_page):
            continue
        _ = chosen.setdefault(ref.to_page, ref.anchor_text)
    managed.update(chosen)
    if not managed:
        return content
    links = [
        f"- [{_encode_link_text(anchor)}]({encode_page_link_target(from_page=from_page, to_page=target)})"
        for target, anchor in sorted(managed.items())
    ]
    rendered_links = "\n".join(links)
    block = f"{_BLOCK_START}\n## Related\n\n{rendered_links}\n{_BLOCK_END}"
    unmanaged = _without_managed_blocks(content).rstrip()
    return f"{unmanaged}\n\n{block}\n"


def _managed_targets(content: str, *, from_page: str) -> dict[str, str]:
    targets: dict[str, str] = {}
    for match in _managed_block_matches(content):
        for link_match in _MANAGED_LINK_RE.finditer(match.group("links")):
            anchor = _decode_link_text(link_match.group(1))
            raw_target = link_match.group(2)
            resolved = resolve_page_link_target(from_page=from_page, raw_target=raw_target)
            if resolved is None:
                continue
            _ = targets.setdefault(resolved, anchor)
    return targets


def _without_managed_blocks(content: str) -> str:
    pieces: list[str] = []
    cursor = 0
    for match in _managed_block_matches(content):
        pieces.append(content[cursor : match.start()])
        cursor = match.end()
    pieces.append(content[cursor:])
    return "".join(pieces)


def _managed_block_matches(content: str) -> list[re.Match[str]]:
    fenced_spans = _fenced_code_spans(content)
    return [
        match
        for match in _BLOCK_RE.finditer(content)
        if not any(match.start() < end and start < match.end() for start, end in fenced_spans)
    ]


def _fenced_code_spans(content: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    active: tuple[str, int, int] | None = None
    offset = 0
    for line in content.splitlines(keepends=True):
        line_end = offset + len(line)
        if active is None:
            opener = _fence_opener(line)
            if opener is not None:
                active = (opener[0], opener[1], offset)
        elif _is_fence_closer(line, delimiter=active[0], minimum_length=active[1]):
            spans.append((active[2], line_end))
            active = None
        offset = line_end
    if active is not None:
        spans.append((active[2], len(content)))
    return spans


def _encode_link_text(anchor: str) -> str:
    return anchor.replace("\\", "\\\\")


def _decode_link_text(anchor: str) -> str:
    return anchor.replace("\\\\", "\\")


def _without_fenced_code(content: str) -> str:
    visible: list[str] = []
    active: tuple[str, int] | None = None
    for line in content.splitlines(keepends=True):
        if active is None:
            opener = _fence_opener(line)
            if opener is None:
                visible.append(line)
            else:
                active = opener
            continue
        if _is_fence_closer(line, delimiter=active[0], minimum_length=active[1]):
            active = None
    return "".join(visible)


def _fence_opener(line: str) -> tuple[str, int] | None:
    stripped = line.rstrip("\r\n")
    indent = len(stripped) - len(stripped.lstrip(" "))
    if indent > 3:
        return None
    candidate = stripped[indent:]
    if not candidate or candidate[0] not in {"`", "~"}:
        return None
    delimiter = candidate[0]
    run_length = len(candidate) - len(candidate.lstrip(delimiter))
    if run_length < 3:
        return None
    if delimiter == "`" and "`" in candidate[run_length:]:
        return None
    return delimiter, run_length


def _is_fence_closer(line: str, *, delimiter: str, minimum_length: int) -> bool:
    stripped = line.rstrip("\r\n")
    indent = len(stripped) - len(stripped.lstrip(" "))
    if indent > 3:
        return False
    candidate = stripped[indent:]
    run_length = len(candidate) - len(candidate.lstrip(delimiter))
    return run_length >= minimum_length and not candidate[run_length:].strip()


def _require_endpoint(*, wiki_root: Path, page_bodies: dict[str, str], path: str) -> None:
    if path not in page_bodies and not (wiki_root / path).is_file():
        raise CrossRefMaterializationError(f"Cross-reference endpoint {path} does not exist after planned page writes.")


def _links_to(content: str, *, from_page: str, to_page: str) -> bool:
    for raw_target in _markdown_link_targets(content):
        if resolve_page_link_target(from_page=from_page, raw_target=raw_target) == to_page:
            return True
    return False


def _markdown_link_targets(content: str) -> list[str]:
    """Extract inline link destinations, including balanced and ``<...>`` forms."""
    return [target for _, target in markdown_inline_links(content)]


def markdown_inline_links(content: str) -> list[tuple[str, str]]:
    """Extract ``(link text, destination)`` pairs from inline Markdown links.

    CommonMark permits an optional quoted or parenthesized title after either a
    bare destination or an angle-bracket destination.  Titles are deliberately
    excluded from the returned destination so link consumers compare the edge,
    not its presentation metadata.
    """
    links: list[tuple[str, str]] = []
    for match in _LINK_OPEN_RE.finditer(content):
        start = match.end()
        if start >= len(content):
            continue
        if content[start] == "<":
            end = _angle_destination_end(content, start=start + 1)
            if end is not None and _optional_title_end(content, start=end + 1) is not None:
                links.append((match.group(1), content[start + 1 : end]))
            continue

        end = _bare_destination_end(content, start=start)
        if end is not None and end > start:
            links.append((match.group(1), content[start:end]))
    return links


def _angle_destination_end(content: str, *, start: int) -> int | None:
    escaped = False
    for index in range(start, len(content)):
        character = content[index]
        if character in "\r\n":
            return None
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character != ">":
            continue
        return index
    return None


def _bare_destination_end(content: str, *, start: int) -> int | None:
    depth = 1
    escaped = False
    for index in range(start, len(content)):
        character = content[index]
        if character in "\r\n":
            return None
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index
        elif character in " \t" and depth == 1 and _optional_title_end(content, start=index) is not None:
            return index
    return None


def _optional_title_end(content: str, *, start: int) -> int | None:
    """Return the outer link's closing ``)`` when a valid optional title follows."""
    cursor = start
    while cursor < len(content) and content[cursor] in " \t":
        cursor += 1
    if cursor >= len(content) or content[cursor] in "\r\n":
        return None
    if content[cursor] == ")":
        return cursor
    if cursor == start or content[cursor] not in {'"', "'", "("}:
        return None

    opener = content[cursor]
    closer = ")" if opener == "(" else opener
    cursor += 1
    escaped = False
    while cursor < len(content):
        character = content[cursor]
        if character in "\r\n":
            return None
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == closer:
            cursor += 1
            break
        cursor += 1
    else:
        return None

    while cursor < len(content) and content[cursor] in " \t":
        cursor += 1
    return cursor if cursor < len(content) and content[cursor] == ")" else None


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

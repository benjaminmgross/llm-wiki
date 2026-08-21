"""Deterministically materialize plan cross-references as Markdown links."""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote, unquote

from mdwiki.plan import CrossRef, Plan

_LINK_OPEN_RE = re.compile(r"\[[^\]\n]+\]\(")
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
    managed = _managed_targets(content, from_page=from_page)
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
    targets: list[str] = []
    for match in _LINK_OPEN_RE.finditer(content):
        start = match.end()
        if start >= len(content):
            continue
        if content[start] == "<":
            end = _angle_destination_end(content, start=start + 1)
            if end is not None:
                targets.append(content[start + 1 : end])
            continue

        end = _balanced_destination_end(content, start=start)
        if end is not None and end > start:
            targets.append(content[start:end])
    return targets


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
        closing = index + 1
        while closing < len(content) and content[closing] in " \t":
            closing += 1
        return index if closing < len(content) and content[closing] == ")" else None
    return None


def _balanced_destination_end(content: str, *, start: int) -> int | None:
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
    return None


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

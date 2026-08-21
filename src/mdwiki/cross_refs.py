"""Deterministically materialize plan cross-references as Markdown links."""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote, unquote

from mdwiki.plan import CrossRef, Plan

_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
_FENCE_RE = re.compile(r"(?ms)^[ \t]*(?:```|~~~).*?^[ \t]*(?:```|~~~)[ \t]*$")
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
        chosen.setdefault(ref.to_page, ref.anchor_text)
    managed.update(chosen)
    if not managed:
        return content
    links = [f"- [{anchor}]({encode_page_link_target(from_page=from_page, to_page=target)})" for target, anchor in sorted(managed.items())]
    rendered_links = "\n".join(links)
    block = f"{_BLOCK_START}\n## Related\n\n{rendered_links}\n{_BLOCK_END}"
    unmanaged = _without_managed_blocks(content).rstrip()
    return f"{unmanaged}\n\n{block}\n"


def _managed_targets(content: str, *, from_page: str) -> dict[str, str]:
    matches = list(_BLOCK_RE.finditer(content))
    targets: dict[str, str] = {}
    for match in matches:
        for anchor, raw_target in re.findall(r"^- \[([^\]]+)\]\(([^)]+)\)$", match.group("links"), flags=re.MULTILINE):
            resolved = resolve_page_link_target(from_page=from_page, raw_target=raw_target)
            if resolved is None:
                continue
            targets.setdefault(resolved, anchor)
    return targets


def _without_managed_blocks(content: str) -> str:
    return _BLOCK_RE.sub("", content)


def _without_fenced_code(content: str) -> str:
    return _FENCE_RE.sub("", content)


def _require_endpoint(*, wiki_root: Path, page_bodies: dict[str, str], path: str) -> None:
    if path not in page_bodies and not (wiki_root / path).is_file():
        raise CrossRefMaterializationError(f"Cross-reference endpoint {path} does not exist after planned page writes.")


def _links_to(content: str, *, from_page: str, to_page: str) -> bool:
    for match in _LINK_RE.finditer(content):
        if resolve_page_link_target(from_page=from_page, raw_target=match.group(1)) == to_page:
            return True
    return False


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

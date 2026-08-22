"""CommonMark interpretation backed by ``markdown-it-py``.

Keep standardized Markdown syntax in the parser library. Callers should add
only mdwiki-specific policy on top of the returned tokens and links.
"""

from __future__ import annotations

from dataclasses import dataclass

from markdown_it import MarkdownIt
from markdown_it.rules_inline import link as parse_link
from markdown_it.rules_inline.state_inline import StateInline
from markdown_it.token import Token

_SOURCE_MARKUP_META = "mdwiki_source_markup"
_SOURCE_TEXT_META = "mdwiki_source_text"


def _link_with_source(state: StateInline, silent: bool) -> bool:
    """Annotate links with source forms while delegating recognition."""
    start = state.pos
    first_new_token = len(state.tokens)
    matched = bool(parse_link(state, silent))
    if not matched or silent:
        return matched

    label_end = state.md.helpers.parseLinkLabel(state, start, True)
    if label_end < 0:
        return matched
    for token in state.tokens[first_new_token:]:
        if token.type == "link_open":
            token.meta[_SOURCE_MARKUP_META] = state.src[start : state.pos]
            token.meta[_SOURCE_TEXT_META] = state.src[start + 1 : label_end]
            break
    return matched


_COMMONMARK = MarkdownIt("commonmark")
_COMMONMARK.inline.ruler.at("link", _link_with_source)


@dataclass(frozen=True)
class MarkdownLink:
    """Semantic link plus parser-derived source forms."""

    text: str
    target: str
    source_markup: str | None
    source_text: str | None


def parse_commonmark(content: str) -> list[Token]:
    """Parse ``content`` using the CommonMark preset."""
    return _COMMONMARK.parse(content)  # type: ignore[no-any-return]


def markdown_links(content: str) -> list[MarkdownLink]:
    """Return semantic links and exact source forms parsed from CommonMark.

    The parser resolves reference-style links and separates optional titles
    from destinations. Links inside code spans and fenced code are excluded by
    CommonMark tokenization rather than by mdwiki-specific syntax handling.
    """
    links: list[MarkdownLink] = []
    for block in parse_commonmark(content):
        if block.type != "inline" or block.children is None:
            continue
        children = block.children
        for index, token in enumerate(children):
            if token.type != "link_open":
                continue
            destination = token.attrGet("href")
            if destination is None:
                continue
            label: list[str] = []
            depth = 1
            for child in children[index + 1 :]:
                if child.type == "link_open":
                    depth += 1
                elif child.type == "link_close":
                    depth -= 1
                    if depth == 0:
                        break
                elif child.type in {"text", "code_inline", "image"}:
                    label.append(child.content)
                elif child.type in {"softbreak", "hardbreak"}:
                    label.append("\n")
            source_markup = token.meta.get(_SOURCE_MARKUP_META)
            source_text = token.meta.get(_SOURCE_TEXT_META)
            links.append(
                MarkdownLink(
                    text="".join(label),
                    target=str(destination),
                    source_markup=source_markup if isinstance(source_markup, str) else None,
                    source_text=source_text if isinstance(source_text, str) else None,
                )
            )
    return links


def markdown_inline_links(content: str) -> list[tuple[str, str]]:
    """Return compatibility ``(text, destination)`` pairs for parsed links."""
    return [(link.text, link.target) for link in markdown_links(content)]

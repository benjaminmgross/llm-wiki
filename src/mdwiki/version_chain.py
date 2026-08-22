"""Page version-chain primitives.

A wiki page's body (the markdown after any YAML frontmatter is stripped) is
hashed with SHA-256. When the page is overwritten, the prior version's hash is
embedded as ``previous_hash:`` in the new version's YAML frontmatter. The
chain is tamper-evident: editing a page outside of mdwiki creates a hash
mismatch that downstream tools can detect.

Phase 4 ships these pure functions plus the ``transaction.write_file`` wiring
that calls them automatically for files under ``wiki/``. CLI surface
(``mdwiki history <page>``) is deferred to a later phase.

Genesis on delete + recreate: ``transaction.write_file`` keys the chain off
``path.exists()``. If a wiki page is deleted (e.g. via ``mdwiki undo``) and
later re-created at the same path, the new version is treated as a fresh
genesis (no ``previous_hash:`` key) — the chain does NOT span the gap. The
``transactions``/``transaction_inverses`` tables retain the prior history
for audit, but downstream tooling that walks the on-disk chain should not
assume monotonic continuity across deletions.

Borrows the pattern from latebit-io/demarkus's ``previous-hash`` chain
(``server/internal/store/store.go:1107-1124``); see the research doc for
context.
"""

from __future__ import annotations

import hashlib
import re

# Frontmatter is detected at the very start of the file. The captured newline
# keeps byte-preserving edits from changing an existing LF/CRLF convention.
# We use a non-greedy match between the open and close fences so a body with
# its own ``---`` separator (e.g. a horizontal rule in markdown) doesn't get
# eaten as the frontmatter close.
_FRONTMATTER_RE = re.compile(
    r"\A---(?P<newline>\r?\n)(?P<inner>.*?)(?P=newline)---(?P=newline)",
    re.DOTALL,
)
_PREVIOUS_HASH_LINE_RE = re.compile(
    r"^previous_hash:[ \t]*[^\r\n]*(?:\r\n|\n)",
    re.MULTILINE,
)


def compute_body_hash(body: str) -> str:
    """Return the SHA-256 hex digest of ``body`` after stripping any frontmatter.

    Parameters
    ----------
    body : str
        Page text. Frontmatter (if present) is stripped before hashing so the
        hash is stable across edits that only touch frontmatter (e.g. updating
        ``previous_hash:`` itself).

    Returns
    -------
    str
        64-character hex SHA-256 digest of the stripped + ``.strip()``-normalized
        body.
    """
    stripped = strip_frontmatter(body).strip()
    return hashlib.sha256(stripped.encode("utf-8")).hexdigest()


def strip_frontmatter(body: str) -> str:
    """Return ``body`` with any leading ``---\\n...\\n---\\n`` block removed."""
    match = _FRONTMATTER_RE.match(body)
    if match is None:
        return body
    return body[match.end() :]


def embed_previous_hash(*, body: str, previous_hash: str) -> str:
    """Return ``body`` with ``previous_hash: <previous_hash>`` set in its frontmatter.

    Parameters
    ----------
    body : str
        Page text. May or may not already have a YAML frontmatter block.
    previous_hash : str
        SHA-256 hex digest of the prior version's body.

    Returns
    -------
    str
        ``body`` with ``previous_hash:`` set in the frontmatter. If the input
        already had a ``previous_hash:`` line, it is replaced. Other frontmatter
        keys are preserved verbatim.

    Notes
    -----
    The frontmatter manipulation is deliberately string-based rather than YAML
    round-tripping. YAML round-tripping would normalize quoting and ordering,
    which would create churn in git diffs every time we touch a page. String
    replacement is precise and minimal.
    """
    match = _FRONTMATTER_RE.match(body)
    if match is None:
        # No frontmatter: prepend a fresh block with just our key.
        newline = "\r\n" if "\r\n" in body else "\n"
        return f"---{newline}previous_hash: {previous_hash}{newline}---{newline}{body}"

    newline = match.group("newline")
    fm_inner = match.group("inner")
    rest = body[match.end() :]

    frontmatter_with_ending = fm_inner + newline
    if _PREVIOUS_HASH_LINE_RE.search(frontmatter_with_ending):
        # Update the existing line in place.
        new_fm_inner = _PREVIOUS_HASH_LINE_RE.sub(f"previous_hash: {previous_hash}{newline}", frontmatter_with_ending)
        new_fm_inner = new_fm_inner[: -len(newline)]
    else:
        # Append our key at the end of the existing frontmatter.
        new_fm_inner = fm_inner.rstrip() + f"{newline}previous_hash: {previous_hash}"

    return f"---{newline}{new_fm_inner}{newline}---{newline}{rest}"


def prepare_wiki_page_content(*, content: str, previous_body: str | None) -> str:
    """Return the exact page text to persist after version-chain preparation.

    This pure boundary is shared by transactional persistence and callers that
    derive caches such as embeddings from the committed representation.
    """
    if previous_body is None:
        return content
    return embed_previous_hash(body=content, previous_hash=compute_body_hash(previous_body))


def extract_previous_hash(body: str) -> str | None:
    """Return the value of ``previous_hash:`` from ``body``'s frontmatter, or ``None``.

    Parameters
    ----------
    body : str
        Page text.

    Returns
    -------
    str or None
        The hex digest if present in the frontmatter, otherwise ``None``.
    """
    match = _FRONTMATTER_RE.match(body)
    if match is None:
        return None
    newline = match.group("newline")
    fm_inner = match.group("inner")
    line = _PREVIOUS_HASH_LINE_RE.search(fm_inner + newline)
    if line is None:
        return None
    # Extract the hex value: everything after "previous_hash:" on the matched line.
    raw = line.group(0)
    _, _, value = raw.partition(":")
    return value.strip().rstrip("\r\n").strip() or None

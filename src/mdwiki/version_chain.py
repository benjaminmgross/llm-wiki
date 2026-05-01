"""Page version-chain primitives.

A wiki page's body (the markdown after any YAML frontmatter is stripped) is
hashed with SHA-256. When the page is overwritten, the prior version's hash is
embedded as ``previous_hash:`` in the new version's YAML frontmatter. The
chain is tamper-evident: editing a page outside of mdwiki creates a hash
mismatch that downstream tools can detect.

Phase 4 ships these pure functions plus the ``transaction.write_file`` wiring
that calls them automatically for files under ``wiki/``. CLI surface
(``mdwiki history <page>``) is deferred to a later phase.

Borrows the pattern from latebit-io/demarkus's ``previous-hash`` chain
(``server/internal/store/store.go:1107-1124``); see the research doc for
context.
"""

from __future__ import annotations

import hashlib
import re

# Frontmatter is detected at the very start of the file: ``---\n...\n---\n``.
# We use a non-greedy match between the open and close fences so a body with
# its own ``---`` separator (e.g. a horizontal rule in markdown) doesn't get
# eaten as the frontmatter close.
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_PREVIOUS_HASH_LINE_RE = re.compile(r"^previous_hash:\s*[^\n]*\n", re.MULTILINE)


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
    return body[match.end():]


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
        return f"---\nprevious_hash: {previous_hash}\n---\n{body}"

    fm_inner = match.group(1)
    rest = body[match.end():]

    if _PREVIOUS_HASH_LINE_RE.search(fm_inner + "\n"):
        # Update the existing line in place.
        new_fm_inner = _PREVIOUS_HASH_LINE_RE.sub(
            f"previous_hash: {previous_hash}\n", fm_inner + "\n"
        ).rstrip("\n")
    else:
        # Append our key at the end of the existing frontmatter.
        new_fm_inner = fm_inner.rstrip() + f"\nprevious_hash: {previous_hash}"

    return f"---\n{new_fm_inner}\n---\n{rest}"


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
    fm_inner = match.group(1)
    line = _PREVIOUS_HASH_LINE_RE.search(fm_inner + "\n")
    if line is None:
        return None
    # Extract the hex value: everything after "previous_hash:" on the matched line.
    raw = line.group(0)
    _, _, value = raw.partition(":")
    return value.strip().rstrip("\n").strip() or None

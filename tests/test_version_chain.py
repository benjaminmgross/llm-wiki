"""Tests for ``mdwiki.version_chain`` — page version-chain primitives.

A wiki page's body is hashed (SHA-256 of stripped body) and the prior version's
hash is embedded as ``previous_hash:`` in the YAML frontmatter when the page
is overwritten. The chain is tamper-evident: any edit by a non-mdwiki actor
creates a hash mismatch detectable by walking the chain.

Phase 4 ships the embedding/extraction pure functions and wires them into
transactional page writes; CLI surface (``mdwiki history``) is deferred.
"""

from __future__ import annotations

import hashlib

import pytest

from mdwiki.version_chain import (
    compute_body_hash,
    embed_previous_hash,
    extract_previous_hash,
    strip_frontmatter,
)


@pytest.mark.unit
def test_compute_body_hash_strips_frontmatter_before_hashing() -> None:
    """Hash is over the stripped body, NOT including any YAML frontmatter."""
    with_fm = "---\ntitle: foo\nprevious_hash: abc\n---\n# Hello\n\nbody"
    without_fm = "# Hello\n\nbody"

    assert compute_body_hash(with_fm) == compute_body_hash(without_fm)
    # And the hash matches a hand-computed sha256 of the stripped body.
    expected = hashlib.sha256(without_fm.strip().encode("utf-8")).hexdigest()
    assert compute_body_hash(without_fm) == expected


@pytest.mark.unit
def test_strip_frontmatter_returns_body_only() -> None:
    text = "---\nfoo: bar\n---\n# Hello\nbody"
    assert strip_frontmatter(text) == "# Hello\nbody"


@pytest.mark.unit
def test_strip_frontmatter_no_frontmatter_returns_input() -> None:
    text = "# Hello\nbody"
    assert strip_frontmatter(text) == text


@pytest.mark.unit
def test_embed_previous_hash_on_page_with_no_frontmatter() -> None:
    """Embedding into a frontmatter-less page creates a frontmatter block."""
    body = "# Hello\n\nbody body body"
    embedded = embed_previous_hash(body=body, previous_hash="abc123")

    assert embedded.startswith("---\n")
    assert "previous_hash: abc123" in embedded
    # Body content survives intact below the frontmatter.
    assert "# Hello" in embedded
    assert "body body body" in embedded


@pytest.mark.unit
def test_embed_previous_hash_on_page_with_existing_frontmatter() -> None:
    """Embedding into an existing frontmatter block updates ``previous_hash`` without losing other keys."""
    body = "---\ntitle: My Page\ndate: 2026-04-30\n---\n# Hello\nbody"
    embedded = embed_previous_hash(body=body, previous_hash="abc123")

    # Existing keys preserved.
    assert "title: My Page" in embedded
    assert "date: 2026-04-30" in embedded
    # New key set.
    assert "previous_hash: abc123" in embedded


@pytest.mark.unit
def test_embed_previous_hash_replaces_existing_previous_hash() -> None:
    """A second embed replaces the first ``previous_hash:`` rather than duplicating it."""
    first = embed_previous_hash(body="# Hello\nbody", previous_hash="OLD")
    second = embed_previous_hash(body=first, previous_hash="NEW")

    assert "previous_hash: NEW" in second
    assert "previous_hash: OLD" not in second
    assert second.count("previous_hash:") == 1


@pytest.mark.unit
def test_extract_previous_hash_from_frontmatter() -> None:
    body = "---\nprevious_hash: abc123\ntitle: x\n---\n# Page\nbody"
    assert extract_previous_hash(body) == "abc123"


@pytest.mark.unit
def test_extract_previous_hash_returns_none_when_absent() -> None:
    body = "---\ntitle: x\n---\n# Page\nbody"
    assert extract_previous_hash(body) is None


@pytest.mark.unit
def test_extract_previous_hash_returns_none_when_no_frontmatter() -> None:
    body = "# Page\nbody"
    assert extract_previous_hash(body) is None


@pytest.mark.unit
def test_round_trip_embed_then_extract() -> None:
    """Embed -> extract returns the embedded hash."""
    body = "# Page\n\nbody"
    embedded = embed_previous_hash(body=body, previous_hash="deadbeef" * 8)
    assert extract_previous_hash(embedded) == "deadbeef" * 8

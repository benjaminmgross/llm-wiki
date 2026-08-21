"""Tests for ``mdwiki.refresh`` — re-discovery of new sources in an initialized wiki."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mdwiki.discover import WikiNotFound
from mdwiki.init import init_wiki
from mdwiki.refresh import refresh_wiki
from mdwiki.state import connect


def _seed_wiki(tmp_path: Path) -> Path:
    """Initialize a wiki at ``tmp_path`` with one source file."""
    (tmp_path / "alpha.md").write_text("# Alpha\n\n## intro\n\nAlpha content.\n")
    init_wiki(tmp_path)
    return tmp_path


@pytest.mark.unit
def test_refresh_wiki_errors_when_uninitialized(tmp_path: Path) -> None:
    # Arrange — no .mdwiki/ directory exists

    # Act & Assert
    with pytest.raises(WikiNotFound):
        refresh_wiki(tmp_path)


@pytest.mark.unit
def test_refresh_wiki_picks_up_new_files(tmp_path: Path) -> None:
    # Arrange
    _seed_wiki(tmp_path)
    (tmp_path / "beta.md").write_text("# Beta\n\n## body\n\nBeta content.\n")

    # Act
    result = refresh_wiki(tmp_path)

    # Assert
    assert result.files_registered == 1
    assert result.created is False
    with connect(tmp_path / ".mdwiki" / "state.db") as conn:
        rows = conn.execute("SELECT original_path FROM sources").fetchall()
    paths = {row["original_path"] for row in rows}
    assert paths == {"alpha.md", "beta.md"}


@pytest.mark.unit
def test_refresh_wiki_dedups_already_registered(tmp_path: Path) -> None:
    # Arrange
    _seed_wiki(tmp_path)

    # Act — refresh with no new files
    result = refresh_wiki(tmp_path)

    # Assert
    assert result.files_registered == 0
    assert result.dedup_skipped >= 1


@pytest.mark.unit
def test_refresh_wiki_preserves_sidecar_merge(tmp_path: Path) -> None:
    # Arrange
    _seed_wiki(tmp_path)
    (tmp_path / "beta.md").write_text("# Beta")

    # Act
    refresh_wiki(tmp_path)

    # Assert — sidecar contains entries from both init AND refresh
    sidecar = json.loads((tmp_path / "raw" / ".sources.json").read_text())
    paths = {entry["original_path"] for entry in sidecar.values()}
    assert paths == {"alpha.md", "beta.md"}


@pytest.mark.unit
def test_refresh_wiki_applies_persisted_exclude_globs(tmp_path: Path) -> None:
    (tmp_path / "alpha.md").write_text("# Alpha")
    init_wiki(tmp_path, exclude_globs=["kbs/**", "**/*.py"])
    excluded_dir = tmp_path / "kbs"
    excluded_dir.mkdir()
    (excluded_dir / "new.md").write_text("# Excluded")
    (tmp_path / "new.py").write_text("print('excluded')")
    (tmp_path / "beta.md").write_text("# Beta")

    result = refresh_wiki(tmp_path)

    assert result.files_registered == 1
    with connect(tmp_path / ".mdwiki" / "state.db") as conn:
        paths = {row["original_path"] for row in conn.execute("SELECT original_path FROM sources")}
    assert paths == {"alpha.md", "beta.md"}

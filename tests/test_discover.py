"""Tests for ``mdwiki.discover`` — locate the wiki root by walking up the filesystem."""

from __future__ import annotations

from pathlib import Path

import pytest

from mdwiki.discover import WikiNotFound, find_wiki


@pytest.mark.unit
def test_find_wiki_at_cwd(tmp_path: Path) -> None:
    (tmp_path / ".mdwiki").mkdir()
    assert find_wiki(tmp_path) == tmp_path


@pytest.mark.unit
def test_find_wiki_in_parent(tmp_path: Path) -> None:
    (tmp_path / ".mdwiki").mkdir()
    sub = tmp_path / "sub"
    sub.mkdir()
    assert find_wiki(sub) == tmp_path


@pytest.mark.unit
def test_find_wiki_in_grandparent(tmp_path: Path) -> None:
    (tmp_path / ".mdwiki").mkdir()
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    assert find_wiki(deep) == tmp_path


@pytest.mark.unit
def test_find_wiki_raises_when_not_found(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    with pytest.raises(WikiNotFound) as excinfo:
        find_wiki(sub)
    assert "no .mdwiki/" in str(excinfo.value).lower()


@pytest.mark.unit
def test_find_wiki_ignores_file_named_mdwiki(tmp_path: Path) -> None:
    (tmp_path / ".mdwiki").write_text("not a dir")
    with pytest.raises(WikiNotFound):
        find_wiki(tmp_path)


@pytest.mark.unit
def test_find_wiki_stops_at_root_without_infinite_loop(tmp_path: Path) -> None:
    sub = tmp_path / "no-wiki-anywhere"
    sub.mkdir()
    with pytest.raises(WikiNotFound):
        find_wiki(sub)


@pytest.mark.unit
def test_find_wiki_respects_start_argument_default_is_cwd(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / ".mdwiki").mkdir()
    monkeypatch.chdir(tmp_path)
    assert find_wiki() == tmp_path

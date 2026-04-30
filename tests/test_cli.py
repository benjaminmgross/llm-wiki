"""Tests for ``mdwiki.cli`` — argparse routing for the ``mdwiki`` command."""

from __future__ import annotations

from pathlib import Path

import pytest

from mdwiki import __version__
from mdwiki.cli import main


@pytest.mark.unit
def test_version_flag_prints_version_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--version"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert __version__ in out


@pytest.mark.unit
def test_init_subcommand_creates_wiki_in_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "a.md").write_text("# A")
    monkeypatch.chdir(tmp_path)

    exit_code = main(["init"])

    assert exit_code == 0
    assert (tmp_path / ".mdwiki").is_dir()
    assert "Initialized wiki" in capsys.readouterr().out


@pytest.mark.unit
def test_init_subcommand_idempotent_message_on_second_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "a.md").write_text("# A")
    monkeypatch.chdir(tmp_path)

    main(["init"])
    capsys.readouterr()

    exit_code = main(["init"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Already initialized" in out


@pytest.mark.unit
def test_init_subcommand_refuses_nested_wiki_with_nonzero_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / ".mdwiki").mkdir()
    child = parent / "child"
    child.mkdir()
    (child / "x.md").write_text("# x")
    monkeypatch.chdir(child)

    exit_code = main(["init"])

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "parent" in err.lower()


@pytest.mark.unit
def test_no_subcommand_prints_help_and_exits_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main([])
    assert exit_code == 2
    out_err = capsys.readouterr()
    assert "usage" in (out_err.out + out_err.err).lower()


@pytest.mark.unit
def test_unknown_subcommand_returns_nonzero(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["definitely-not-a-command"])
    assert exit_code != 0

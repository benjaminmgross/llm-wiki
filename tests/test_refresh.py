"""Tests for ``mdwiki.refresh`` — re-discovery of new sources in an initialized wiki."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

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


def _enable_vision(wiki_root: Path, *, provider: str = "session") -> None:
    (wiki_root / ".mdwiki" / "config.toml").write_text(
        f'[llm]\nprovider = "{provider}"\n\n[loaders.pdf]\nvision_fallback = true\n\n[loaders.image]\nenabled = true\n'
    )


@pytest.mark.unit
def test_refresh_does_not_build_a_provider_when_vision_is_off(tmp_path: Path, mocker: MockerFixture) -> None:
    """Default wikis must not need credentials, or an agent CLI, just to register files."""
    _seed_wiki(tmp_path)
    build = mocker.patch("mdwiki.init.build_provider_from_config")

    refresh_wiki(tmp_path)

    build.assert_not_called()


@pytest.mark.unit
def test_refresh_hands_the_configured_provider_to_vision_loaders(tmp_path: Path, mocker: MockerFixture) -> None:
    """With vision enabled, an image is transcribed through the configured provider."""
    _seed_wiki(tmp_path)
    _enable_vision(tmp_path)
    (tmp_path / "scan.png").write_bytes(b"\x89PNG fake")
    provider = mocker.MagicMock()
    provider.describe_image.return_value = "# Scan\n\nINVOICE 4721\n"
    mocker.patch("mdwiki.init.build_provider_from_config", return_value=provider)

    result = refresh_wiki(tmp_path)

    assert result.files_registered == 1
    provider.describe_image.assert_called_once()
    sidecar = json.loads((tmp_path / "raw" / ".sources.json").read_text())
    image_entry = next(entry for entry in sidecar.values() if entry["original_path"] == "scan.png")
    assert "INVOICE 4721" in (tmp_path / image_entry["raw_path"]).read_text()


@pytest.mark.unit
def test_refresh_continues_without_vision_when_the_provider_cannot_be_built(
    tmp_path: Path,
    mocker: MockerFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing key or CLI must not stop ordinary files from registering."""
    _seed_wiki(tmp_path)
    _enable_vision(tmp_path, provider="anthropic")
    (tmp_path / "beta.md").write_text("# Beta\n\n## body\n\nBeta content.\n")
    from mdwiki.llm.anthropic import MissingAPIKeyError

    mocker.patch("mdwiki.init.build_provider_from_config", side_effect=MissingAPIKeyError("no credentials"))

    result = refresh_wiki(tmp_path)

    assert result.files_registered == 1
    err = capsys.readouterr().err
    assert "vision" in err
    assert "no credentials" in err


@pytest.mark.unit
def test_refresh_stops_when_the_vision_provider_is_logged_out(tmp_path: Path, mocker: MockerFixture) -> None:
    """Every image would fail the same way; skipping them one by one hides the cause."""
    from mdwiki.llm.session import SessionCliError

    _seed_wiki(tmp_path)
    _enable_vision(tmp_path)
    (tmp_path / "one.png").write_bytes(b"\x89PNG one")
    (tmp_path / "two.png").write_bytes(b"\x89PNG two")
    provider = mocker.MagicMock()
    provider.describe_image.side_effect = SessionCliError("claude CLI is not signed in", kind="not-logged-in")
    mocker.patch("mdwiki.init.build_provider_from_config", return_value=provider)

    with pytest.raises(SessionCliError):
        refresh_wiki(tmp_path)

    assert provider.describe_image.call_count == 1


@pytest.mark.integration
@pytest.mark.parametrize("initial_registration", [False, True])
def test_vision_abort_preserves_registration_for_retry_and_rebuild(
    tmp_path: Path, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch, initial_registration: bool
) -> None:
    from mdwiki.init import DEFAULT_CONFIG
    from mdwiki.llm.session import SessionCliError
    from mdwiki.rebuild import rebuild_wiki

    if initial_registration:
        monkeypatch.setitem(DEFAULT_CONFIG["loaders"]["image"], "enabled", True)
        (tmp_path / "alpha.md").write_text("# Alpha")
    else:
        _seed_wiki(tmp_path)
        _enable_vision(tmp_path)
    (tmp_path / "beta.md").write_text("# Beta")
    (tmp_path / "scan.png").write_bytes(b"\x89PNG fake")
    provider = mocker.MagicMock()
    provider.describe_image.side_effect = SessionCliError("usage limit reached", kind="rate-limited")
    mocker.patch("mdwiki.init.build_provider_from_config", return_value=provider)

    with pytest.raises(SessionCliError, match="usage limit"):
        (init_wiki if initial_registration else refresh_wiki)(tmp_path)

    sidecar_path = tmp_path / "raw" / ".sources.json"
    sidecar = json.loads(sidecar_path.read_text())
    assert {entry["original_path"] for entry in sidecar.values()} == {"alpha.md", "beta.md"}
    db_path = tmp_path / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        assert {row["id"] for row in conn.execute("SELECT id FROM sources")} == set(sidecar)
    assert all((tmp_path / entry["raw_path"]).is_file() for entry in sidecar.values())

    provider.describe_image.side_effect = None
    provider.describe_image.return_value = "# Scan\n\nINVOICE 4721"
    assert refresh_wiki(tmp_path).files_registered == 1
    sidecar = json.loads(sidecar_path.read_text())
    assert {entry["original_path"] for entry in sidecar.values()} == {"alpha.md", "beta.md", "scan.png"}
    with connect(db_path) as conn:
        conn.execute("DELETE FROM sources")
        conn.commit()
    assert rebuild_wiki(tmp_path).sources_restored == 3
    with connect(db_path) as conn:
        rows = conn.execute("SELECT original_path, status FROM sources").fetchall()
    assert {row["original_path"] for row in rows} == {"alpha.md", "beta.md", "scan.png"}
    assert all(row["status"] == "pending" for row in rows)


@pytest.mark.integration
def test_registration_sidecar_replace_failure_preserves_previous_metadata(tmp_path: Path, mocker: MockerFixture) -> None:
    _seed_wiki(tmp_path)
    sidecar_path = tmp_path / "raw" / ".sources.json"
    before = sidecar_path.read_bytes()
    (tmp_path / "beta.md").write_text("# Beta")
    replace = mocker.patch("mdwiki.init.os.replace", side_effect=OSError("disk failure"))

    with pytest.raises(OSError, match="disk failure"):
        refresh_wiki(tmp_path)

    assert sidecar_path.read_bytes() == before
    assert list(sidecar_path.parent.glob(".sources.json.tmp-*")) == []
    with connect(tmp_path / ".mdwiki" / "state.db") as conn:
        assert {row["original_path"] for row in conn.execute("SELECT original_path FROM sources")} == {"alpha.md"}
    mocker.stop(replace)
    assert refresh_wiki(tmp_path).files_registered == 1
    assert {entry["original_path"] for entry in json.loads(sidecar_path.read_text()).values()} == {"alpha.md", "beta.md"}


@pytest.mark.unit
def test_refresh_skips_one_image_when_the_failure_is_per_call(tmp_path: Path, mocker: MockerFixture) -> None:
    from mdwiki.llm.session import SessionCliError

    _seed_wiki(tmp_path)
    _enable_vision(tmp_path)
    (tmp_path / "one.png").write_bytes(b"\x89PNG one")
    (tmp_path / "two.png").write_bytes(b"\x89PNG two")
    provider = mocker.MagicMock()
    provider.describe_image.side_effect = [SessionCliError("timed out", kind="timeout"), "# Two\n\ntext\n"]
    mocker.patch("mdwiki.init.build_provider_from_config", return_value=provider)

    result = refresh_wiki(tmp_path)

    assert result.files_registered == 1
    assert provider.describe_image.call_count == 2


@pytest.mark.unit
def test_refresh_does_not_swallow_an_unexpected_provider_construction_error(tmp_path: Path, mocker: MockerFixture) -> None:
    _seed_wiki(tmp_path)
    _enable_vision(tmp_path)
    mocker.patch("mdwiki.init.build_provider_from_config", side_effect=RuntimeError("bug"))

    with pytest.raises(RuntimeError, match="bug"):
        refresh_wiki(tmp_path)


@pytest.mark.unit
def test_refresh_wiki_respects_persisted_config_exclude_globs(tmp_path: Path) -> None:
    _seed_wiki(tmp_path)
    config_path = tmp_path / ".mdwiki" / "config.toml"
    config = config_path.read_text()
    config_path.write_text(
        config.replace(
            "globs = []",
            'globs = ["**/*", "!/*.md", "!/allowed/**/*.md"]',
        )
    )
    (tmp_path / "allowed").mkdir()
    (tmp_path / "allowed" / "keep.md").write_text("# Keep")
    (tmp_path / "excluded").mkdir()
    (tmp_path / "excluded" / "drop.md").write_text("# Drop")

    result = refresh_wiki(tmp_path)

    assert result.files_registered == 1
    with connect(tmp_path / ".mdwiki" / "state.db") as conn:
        paths = {row["original_path"] for row in conn.execute("SELECT original_path FROM sources")}
    assert paths == {"alpha.md", "allowed/keep.md"}

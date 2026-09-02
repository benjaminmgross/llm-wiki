"""Tests for ``mdwiki.cli`` — argparse routing for the ``mdwiki`` command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from mdwiki import __version__
from mdwiki.cli import main
from mdwiki.init import init_wiki


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
def test_init_bootstrap_chains_ingest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mocker,  # type: ignore[no-untyped-def]
) -> None:
    """init --bootstrap should run init AND iterate over pending sources."""
    import json

    from mdwiki.llm.base import CompleteResult

    (tmp_path / "a.md").write_text("# A\n\n## intro\n\nThis paper introduces attention sinks for long contexts.\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    plan = json.dumps(
        {
            "verdict": "ingest",
            "rationale": "x",
            "updates": [],
            "new_pages": [
                {
                    "path": "wiki/concepts/x.md",
                    "kind": "concept",
                    "content": "# X\n\nbody",
                    "claims": [{"source_section_id": "a.md/intro", "quote": "this paper introduces attention sinks for long contexts"}],
                }
            ],
            "cross_refs": [],
        }
    )
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.complete",
        return_value=CompleteResult(text=plan, input_tokens=10, output_tokens=10),
    )

    exit_code = main(["init", "--bootstrap"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Bootstrap done" in out
    assert "1 of 1" in out
    assert (tmp_path / "wiki" / "concepts" / "x.md").exists()


@pytest.mark.unit
def test_init_bootstrap_returns_nonzero_on_partial_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    from mdwiki.ingest import IngestResult

    (tmp_path / "a.md").write_text("# A")
    monkeypatch.chdir(tmp_path)
    mocker.patch(
        "mdwiki.cli.ingest_many",
        return_value=[IngestResult(source_id="abc", applied=False, message="failed: bad source")],
    )

    assert main(["init", "--bootstrap"]) == 1


@pytest.mark.unit
def test_ingest_pending_returns_nonzero_on_partial_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    from mdwiki.ingest import IngestResult

    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    mocker.patch(
        "mdwiki.cli.ingest_many",
        return_value=[IngestResult(source_id="abc", applied=False, message="failed: bad source")],
    )

    assert main(["ingest", "--pending"]) == 1


@pytest.mark.unit
def test_init_subcommand_errors_on_corrupt_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A pre-existing corrupt raw/.sources.json on a fresh init must error cleanly.

    Reachable in the wild: a user follows the README's `mdwiki rebuild` recovery
    flow (delete .mdwiki/, keep raw/) but raw/.sources.json got truncated.
    Init's _register_sources reads any pre-existing sidecar; the CLI must
    surface SidecarCorruptError, not a raw traceback.
    """
    # Arrange — pre-create raw/ with a corrupt sidecar; do NOT create .mdwiki/
    (tmp_path / "alpha.md").write_text("# Alpha")
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    (raw_dir / ".sources.json").write_text("{not valid")
    monkeypatch.chdir(tmp_path)

    # Act — init runs the full path because .mdwiki/ does not exist
    exit_code = main(["init"])

    # Assert — clean error message + nonzero exit
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert ".sources.json" in err


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


def _seed_wiki(tmp_path: Path) -> Path:
    """Helper: init a fresh wiki with two .md files; chdir into it not required."""
    (tmp_path / "alpha.md").write_text("# Alpha")
    (tmp_path / "beta.md").write_text("# Beta")
    main(["init", str(tmp_path)])
    return tmp_path


@pytest.mark.unit
def test_status_subcommand_shows_pending_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_wiki(tmp_path)
    capsys.readouterr()
    monkeypatch.chdir(tmp_path)

    exit_code = main(["status"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "2 pending" in out
    assert "0 ingested" in out


@pytest.mark.unit
def test_status_subcommand_errors_outside_wiki(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    exit_code = main(["status"])
    assert exit_code == 1
    assert "no .mdwiki/" in capsys.readouterr().err.lower()


@pytest.mark.unit
def test_source_subcommand_prints_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mdwiki.state import connect

    _seed_wiki(tmp_path)
    capsys.readouterr()
    monkeypatch.chdir(tmp_path)
    with connect(tmp_path / ".mdwiki" / "state.db") as conn:
        first_id = conn.execute("SELECT id FROM sources WHERE original_path = 'alpha.md'").fetchone()["id"]

    exit_code = main(["source", first_id[:6]])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert first_id in out
    assert "alpha.md" in out


@pytest.mark.unit
def test_source_subcommand_errors_on_no_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_wiki(tmp_path)
    capsys.readouterr()
    monkeypatch.chdir(tmp_path)
    exit_code = main(["source", "deadbeef"])
    assert exit_code == 1
    assert "no source" in capsys.readouterr().err.lower()


@pytest.mark.unit
def test_source_subcommand_disambiguates_on_multiple_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Two sources sharing a hash prefix → ``mdwiki source <prefix>`` lists both and exits non-zero."""
    from mdwiki.state import connect

    _seed_wiki(tmp_path)
    capsys.readouterr()
    monkeypatch.chdir(tmp_path)
    with connect(tmp_path / ".mdwiki" / "state.db") as conn:
        conn.execute(
            "INSERT INTO sources (id, original_path, raw_path, content_hash, mtime, status) VALUES (?, ?, ?, ?, ?, ?)",
            ("aaaa11112222", "fake-1.md", "raw/fake-1.md", "a" * 64, 1.0, "pending"),
        )
        conn.execute(
            "INSERT INTO sources (id, original_path, raw_path, content_hash, mtime, status) VALUES (?, ?, ?, ?, ?, ?)",
            ("aaaa33334444", "fake-2.md", "raw/fake-2.md", "a" * 64, 1.0, "pending"),
        )
        conn.commit()

    exit_code = main(["source", "aaaa"])

    assert exit_code == 1
    text = capsys.readouterr().err
    assert "ambiguous" in text.lower()
    assert "aaaa11112222" in text
    assert "aaaa33334444" in text


@pytest.mark.unit
def test_doctor_subcommand_green(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pytest_mock import MockerFixture  # noqa: F401 — for type readers

    _seed_wiki(tmp_path)
    capsys.readouterr()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    from unittest.mock import patch

    from mdwiki.llm.base import PingResult

    fake = PingResult(provider="anthropic", model="claude-sonnet-4-6", latency_ms=12.3, ok=True, message="pong")
    with patch("mdwiki.llm.anthropic.AnthropicProvider.ping", return_value=fake):
        exit_code = main(["doctor"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "anthropic" in out
    assert "claude-sonnet-4-6" in out


@pytest.mark.unit
def test_doctor_subcommand_red_returns_nonzero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_wiki(tmp_path)
    capsys.readouterr()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-bad")

    from unittest.mock import patch

    from mdwiki.llm.base import PingResult

    fake = PingResult(provider="anthropic", model="claude-sonnet-4-6", latency_ms=8.0, ok=False, message="auth failed")
    with patch("mdwiki.llm.anthropic.AnthropicProvider.ping", return_value=fake):
        exit_code = main(["doctor"])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "fail" in out.lower() or "auth" in out.lower()


@pytest.mark.unit
def test_doctor_without_api_key_returns_nonzero_with_clear_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_wiki(tmp_path)
    capsys.readouterr()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    exit_code = main(["doctor"])

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY" in err


@pytest.mark.unit
def test_rebuild_subcommand_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed_wiki(tmp_path)
    capsys.readouterr()
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".mdwiki" / "state.db").unlink()

    exit_code = main(["rebuild"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "2 source(s) restored" in out
    assert (tmp_path / ".mdwiki" / "state.db").is_file()


@pytest.mark.unit
def test_rebuild_pages_subcommand_populates_page_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mocker: MockerFixture,
) -> None:
    _seed_wiki(tmp_path)
    capsys.readouterr()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    (tmp_path / "wiki" / "concepts" / "existing.md").write_text("# Existing\n\nBody.")
    fake_embedder = mocker.Mock()
    fake_embedder.embed_text.return_value = [1.0, 0.0, 0.0]
    mocker.patch("mdwiki.page_index.get_default_embedder", return_value=fake_embedder)

    exit_code = main(["rebuild", "--pages"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "page index rebuilt" in out.lower()
    from mdwiki.state import connect

    with connect(tmp_path / ".mdwiki" / "state.db") as conn:
        count = conn.execute("SELECT COUNT(*) AS c FROM pages").fetchone()["c"]
    assert count == 1


@pytest.mark.unit
def test_refresh_subcommand_picks_up_new_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Arrange
    (tmp_path / "alpha.md").write_text("# Alpha")
    monkeypatch.chdir(tmp_path)
    main(["init"])
    capsys.readouterr()
    (tmp_path / "beta.md").write_text("# Beta")

    # Act
    exit_code = main(["refresh"])

    # Assert
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Refreshed wiki" in out
    assert "Registered 1 new source" in out


@pytest.mark.unit
def test_refresh_subcommand_accepts_explicit_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """refresh accepts a positional path arg; the wiki is located via that path, not cwd.

    Deliberately does NOT use ``monkeypatch.chdir`` — if a future refactor drops
    ``args.path`` and falls back to cwd, this test must fail.
    """
    # Arrange — init a wiki, then drop a new file. cwd is NOT the wiki.
    (tmp_path / "alpha.md").write_text("# Alpha")
    main(["init", str(tmp_path)])
    capsys.readouterr()
    (tmp_path / "beta.md").write_text("# Beta")

    # Act — call refresh from outside the wiki, passing the path explicitly
    exit_code = main(["refresh", str(tmp_path)])

    # Assert
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Registered 1 new source" in out


@pytest.mark.unit
def test_refresh_subcommand_no_new_files_returns_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "alpha.md").write_text("# Alpha")
    monkeypatch.chdir(tmp_path)
    main(["init"])
    capsys.readouterr()
    exit_code = main(["refresh"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Registered 0 new source" in out
    assert "already-registered" in out


@pytest.mark.unit
def test_refresh_subcommand_errors_outside_wiki(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Arrange
    monkeypatch.chdir(tmp_path)

    # Act
    exit_code = main(["refresh"])

    # Assert
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "mdwiki init" in err


@pytest.mark.unit
def test_refresh_subcommand_errors_on_corrupt_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A corrupt .mdwiki/config.toml must produce a clean error, not a TOMLDecodeError traceback."""
    # Arrange — init a wiki, then corrupt config.toml
    (tmp_path / "alpha.md").write_text("# Alpha")
    monkeypatch.chdir(tmp_path)
    main(["init"])
    capsys.readouterr()
    (tmp_path / ".mdwiki" / "config.toml").write_text("not [valid toml")

    # Act
    exit_code = main(["refresh"])

    # Assert — exit 1, message names config.toml
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert "config.toml" in err


@pytest.mark.unit
def test_refresh_subcommand_errors_on_corrupt_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A truncated raw/.sources.json must surface as a clean error, not a JSONDecodeError traceback.

    The CLI must catch SidecarCorruptError and print a remediation hint;
    silent fall-through to an empty dict would clobber the salvageable
    sidecar on the next write.
    """
    # Arrange — init a wiki, then corrupt its sidecar
    (tmp_path / "alpha.md").write_text("# Alpha")
    monkeypatch.chdir(tmp_path)
    main(["init"])
    capsys.readouterr()
    (tmp_path / "raw" / ".sources.json").write_text("{not valid")

    # Act — refresh with a corrupt sidecar
    exit_code = main(["refresh"])

    # Assert — clean error message + nonzero exit
    assert exit_code == 1
    err = capsys.readouterr().err
    assert "error:" in err
    assert ".sources.json" in err


@pytest.mark.unit
def test_refresh_bootstrap_batch_chains_submission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mocker,  # type: ignore[no-untyped-def]
) -> None:
    """refresh --bootstrap-batch should rescan and route through _run_bootstrap_batch."""
    # Arrange — init a wiki, drop a new file so refresh registers something
    monkeypatch.chdir(tmp_path)
    main(["init"])
    capsys.readouterr()
    (tmp_path / "beta.md").write_text("# Beta")
    batch_mock = mocker.patch("mdwiki.cli._run_bootstrap_batch", return_value=0)

    # Act
    exit_code = main(["refresh", "--bootstrap-batch", "--yes"])

    # Assert — _run_bootstrap_batch was called with the resolved wiki root + yes=True
    assert exit_code == 0
    batch_mock.assert_called_once()
    call_kwargs = batch_mock.call_args.kwargs
    assert call_kwargs == {"yes": True}
    (positional_root,) = batch_mock.call_args.args
    assert positional_root == tmp_path.resolve()


@pytest.mark.unit
def test_refresh_bootstrap_chains_when_no_new_files_but_pending_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mocker,  # type: ignore[no-untyped-def]
) -> None:
    """refresh --bootstrap must reach the ingest path even when no NEW files were registered.

    A prior crashed bootstrap can leave pending rows in state.db. ``refresh
    --bootstrap`` is the user's recovery one-liner; silently no-op-ing
    when ``files_registered == 0`` would drop their intent.
    """
    # Arrange — init a wiki (registers alpha.md as pending). Refresh will register 0 new.
    (tmp_path / "alpha.md").write_text("# Alpha")
    monkeypatch.chdir(tmp_path)
    main(["init"])
    capsys.readouterr()
    sync_mock = mocker.patch("mdwiki.cli._run_bootstrap_sync", return_value=0)

    # Act — refresh sees no new files but the user explicitly asked for --bootstrap
    exit_code = main(["refresh", "--bootstrap"])

    # Assert — bootstrap-sync chain still fired with files_registered=0
    assert exit_code == 0
    sync_mock.assert_called_once()
    call_kwargs = sync_mock.call_args.kwargs
    assert call_kwargs == {"files_registered": 0}
    (positional_root,) = sync_mock.call_args.args
    assert positional_root == tmp_path.resolve()


@pytest.mark.unit
def test_refresh_bootstrap_chains_ingest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mocker,  # type: ignore[no-untyped-def]
) -> None:
    """refresh --bootstrap should rescan AND iterate over pending sources."""
    import json

    from mdwiki.llm.base import CompleteResult

    # Arrange — init an empty folder, then drop a new source so refresh has work to do
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    main(["init"])
    capsys.readouterr()
    (tmp_path / "beta.md").write_text("# Beta\n\n## body\n\nThis paper introduces attention sinks for long contexts.\n")

    plan = json.dumps(
        {
            "verdict": "ingest",
            "rationale": "x",
            "updates": [],
            "new_pages": [
                {
                    "path": "wiki/concepts/sinks.md",
                    "kind": "concept",
                    "content": "# Sinks\n\nbody",
                    "claims": [
                        {
                            "source_section_id": "beta.md/body",
                            "quote": "this paper introduces attention sinks for long contexts",
                        }
                    ],
                }
            ],
            "cross_refs": [],
        }
    )
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.complete",
        return_value=CompleteResult(text=plan, input_tokens=10, output_tokens=10),
    )

    # Act
    exit_code = main(["refresh", "--bootstrap"])

    # Assert
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Bootstrap done" in out
    assert "1 of 1" in out
    assert (tmp_path / "wiki" / "concepts" / "sinks.md").exists()


@pytest.mark.unit
def test_refresh_help_describes_provider_aware_bootstrap_batch(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["refresh", "--help"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "configured provider" in " ".join(out.split())
    assert "Anthropic Batch API" not in out


def test_cli_help_describes_failed_source_status_and_retry(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--help"]) == 0
    top_level_help = " ".join(capsys.readouterr().out.split())
    assert "pending/failed/ingested counts" in top_level_help

    assert main(["ingest", "--help"]) == 0
    ingest_help = " ".join(capsys.readouterr().out.split())
    assert "pending or failed" in ingest_help


def test_session_ingest_pending_emits_unique_json_manifest(tmp_path: Path, monkeypatch, capsys) -> None:
    (tmp_path / "b.md").write_text("# B\n\nbody")
    (tmp_path / "a.md").write_text("# A\n\nbody")
    init_wiki(tmp_path)
    monkeypatch.chdir(tmp_path)

    exit_code = main(["session-ingest", "pending"])
    manifest = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert [source["original_path"] for source in manifest] == ["a.md", "b.md"]
    assert len({source["id"] for source in manifest}) == 2


def test_session_ingest_prepare_writes_editable_envelope(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "source.md").write_text("# Source\n\n## Evidence\n\nDurable source evidence belongs in the wiki.")
    init_wiki(tmp_path)
    monkeypatch.chdir(tmp_path)
    envelope_path = tmp_path / ".mdwiki" / "session-plans" / "plan.json"

    exit_code = main(["session-ingest", "prepare", "source.md", "--output", str(envelope_path)])
    envelope = json.loads(envelope_path.read_text())

    assert exit_code == 0
    assert envelope["source"]["original_path"] == "source.md"
    assert envelope["plan"] is None


def test_session_ingest_apply_returns_distinct_exit_for_invalidated_plan(tmp_path: Path, monkeypatch, capsys) -> None:
    from mdwiki.session_ingest import prepare_session_plan

    (tmp_path / "source.md").write_text("# Source\n\n## Evidence\n\nDurable source evidence belongs in the wiki.")
    init_wiki(tmp_path)
    monkeypatch.chdir(tmp_path)
    envelope = prepare_session_plan(tmp_path, "source.md").to_dict()
    envelope["plan"] = {
        "verdict": "low-quality",
        "rationale": "No load-bearing content.",
        "updates": [],
        "new_pages": [],
        "cross_refs": [],
    }
    (tmp_path / ".mdwiki" / "schema.md").write_text("changed after preparation")
    envelope_path = tmp_path / "stale-plan.json"
    envelope_path.write_text(json.dumps(envelope))

    exit_code = main(["session-ingest", "apply", str(envelope_path)])

    assert exit_code == 3
    assert "invalidated" in capsys.readouterr().err


@pytest.mark.integration
def test_refresh_bootstrap_batch_openai_compatible_fallback_keeps_initiative_wiki_coherent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mocker: MockerFixture,
) -> None:
    import json

    from mdwiki.init import init_wiki
    from mdwiki.llm.base import CompleteResult
    from mdwiki.state import connect
    from mdwiki.status import get_status

    class StubEmbedder:
        def embed_text(self, _text: str) -> list[float]:
            return [1.0, 0.0, 0.0]

    (tmp_path / "capital.md").write_text(
        "# Capital Raise\n\n## Intro\n\nThe team is coordinating investor outreach for the capital raise.\n"
    )
    init_wiki(tmp_path, profile="initiative")
    config_path = tmp_path / ".mdwiki" / "config.toml"
    config_path.write_text(
        config_path.read_text().replace(
            'provider = "anthropic"\nmodel = "claude-sonnet-4-6"',
            'provider = "openai-compatible"\n'
            'model = "local-initiative-model"\n\n'
            "[llm.openai_compatible]\n"
            'base_url = "http://localhost:8000/v1"',
        )
    )
    plan = json.dumps(
        {
            "verdict": "ingest",
            "rationale": "Tracks the investor outreach workstream.",
            "updates": [],
            "new_pages": [
                {
                    "path": "wiki/workstreams/investor-outreach.md",
                    "kind": "workstream",
                    "content": "# Investor Outreach\n\nCapital raise outreach workstream.",
                    "claims": [
                        {
                            "source_section_id": "capital.md/Intro",
                            "quote": "team is coordinating investor outreach for the capital raise",
                        }
                    ],
                }
            ],
            "cross_refs": [],
        }
    )
    complete = mocker.patch(
        "mdwiki.llm.openai_compatible.OpenAICompatibleProvider.complete",
        return_value=CompleteResult(text=plan, input_tokens=10, output_tokens=10),
    )
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.__init__",
        side_effect=AssertionError("Anthropic must not be constructed"),
    )
    mocker.patch("mdwiki.bootstrap.get_default_embedder", return_value=StubEmbedder())
    monkeypatch.chdir(tmp_path)

    exit_code = main(["refresh", "--bootstrap-batch", "--yes"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "openai-compatible" in captured.out
    assert "synchronous ingest fallback" in captured.out
    assert complete.call_count == 1
    assert (tmp_path / "wiki" / "workstreams" / "investor-outreach.md").is_file()
    assert (tmp_path / "wiki" / "index.md").is_file()
    assert "ingest capital.md" in (tmp_path / "wiki" / "log.md").read_text()
    report = get_status(tmp_path)
    assert report.pending == 0
    assert report.failed == 0
    assert report.ingested == 1
    with connect(tmp_path / ".mdwiki" / "state.db") as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM pages WHERE kind != 'source'").fetchone()["c"] == 1
        assert conn.execute("SELECT COUNT(*) AS c FROM backrefs").fetchone()["c"] == 1
    sidecar = json.loads((tmp_path / "raw" / ".sources.json").read_text())
    source_meta = next(iter(sidecar.values()))
    assert source_meta["status"] == "ingested"
    assert source_meta["failure_reason"] is None


@pytest.mark.unit
def test_init_pointers_flag_routes_to_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "a.md").write_text("# A")
    monkeypatch.chdir(tmp_path)

    assert main(["init", "--pointers=claude,copilot"]) == 0
    assert (tmp_path / "CLAUDE.md").is_file()
    assert (tmp_path / ".github" / "copilot-instructions.md").is_file()
    assert not (tmp_path / "AGENTS.md").exists()
    assert "pointer" in capsys.readouterr().out.lower()

    other = tmp_path.parent / (tmp_path.name + "-b")
    other.mkdir()
    (other / "b.md").write_text("# B")
    monkeypatch.chdir(other)
    assert main(["init", "--pointers=bogus"]) == 2
    assert "pointers" in capsys.readouterr().err


@pytest.mark.unit
def test_ingest_pending_overview_flag_refreshes_overview_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], mocker: MockerFixture
) -> None:
    from mdwiki.llm.base import CompleteResult
    from mdwiki.overview import OVERVIEW_PATH

    (tmp_path / "a.md").write_text("# A\n\n## intro\n\nThis paper introduces attention sinks for long contexts.\n")
    (tmp_path / "b.md").write_text("# B\n\n## intro\n\nThis paper introduces retrieval for long contexts.\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    init_wiki(tmp_path, pointers=())

    def plan(slug: str, source: str, quote: str) -> str:
        return json.dumps(
            {
                "verdict": "ingest",
                "rationale": "x",
                "updates": [],
                "new_pages": [{"path": f"wiki/concepts/{slug}.md", "kind": "concept", "content": f"# {slug}\n\nbody", "claims": [{"source_section_id": f"{source}/intro", "quote": quote}]}],
                "cross_refs": [],
            }
        )

    responses = [
        CompleteResult(text=plan("sinks", "a.md", "this paper introduces attention sinks for long contexts"), input_tokens=1, output_tokens=1),
        CompleteResult(text=plan("retrieval", "b.md", "this paper introduces retrieval for long contexts"), input_tokens=1, output_tokens=1),
        CompleteResult(text="# Overview\n\nTwo concepts.\n", input_tokens=1, output_tokens=1),
    ]
    complete = mocker.patch("mdwiki.llm.anthropic.AnthropicProvider.complete", side_effect=responses)
    mocker.patch("mdwiki.ingest.get_default_embedder", return_value=type("E", (), {"embed_text": lambda self, t: [1.0, 0.0]})())

    assert main(["ingest", "--pending", "--overview"]) == 0
    out = capsys.readouterr().out
    assert complete.call_count == 3
    assert (tmp_path / OVERVIEW_PATH).read_text().endswith("Two concepts.\n")
    assert OVERVIEW_PATH in out


@pytest.mark.unit
def test_lint_report_grouped_by_severity_with_numbers_and_fix_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import time

    from mdwiki.state import connect

    monkeypatch.chdir(tmp_path)
    init_wiki(tmp_path, pointers=())
    pages = {"a": "# a\n\n[gone](gone.md)\n", "b": "# b\n\nno links [unverified-quote]\n"}
    for name, content in pages.items():
        page = tmp_path / "wiki" / "concepts" / f"{name}.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(content)
        with connect(tmp_path / ".mdwiki" / "state.db") as conn:
            conn.execute("INSERT INTO pages (path, kind, last_touched_at) VALUES (?, 'concept', ?)", (f"wiki/concepts/{name}.md", time.time()))
            conn.commit()

    assert main(["lint"]) == 0
    out = capsys.readouterr().out
    assert "## warn" in out and "## info" in out
    assert out.index("## warn") < out.index("## info")
    assert "[1] " in out and "[2] " in out
    assert "--fix=1,3" in out or "--fix=<n>" in out

    assert main(["lint", "--fix=1", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "applied 1" in out

    assert main(["lint", "--fix=zero,1"]) == 2
    assert "--fix" in capsys.readouterr().err

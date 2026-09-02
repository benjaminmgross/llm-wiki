"""Tests for the ``mdwiki skill`` runtime command (Phase 6).

Borrows the pattern from llmwiki-cli's ``wiki skill`` command (research doc
section 5): a CLI subcommand that prints the wiki's schema + an agent-facing
how-to guide to stdout. Lets a Claude Code instance opening a wiki folder
self-orient with a single ``mdwiki skill`` call.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mdwiki.cli import main
from mdwiki.init import init_wiki


@pytest.mark.unit
def test_skill_prints_schema_text(tmp_path: Path, monkeypatch, capsys) -> None:
    """``mdwiki skill`` prints the wiki's .mdwiki/schema.md content."""
    init_wiki(tmp_path, profile="working-dir")
    monkeypatch.chdir(tmp_path)

    rc = main(["skill"])
    out = capsys.readouterr().out

    assert rc == 0
    # The default schema declares "Page kinds" — assert it shows up.
    assert "Page kinds" in out
    # The schema mentions entity / concept / synthesis.
    assert "entity" in out.lower()
    assert "concept" in out.lower()


@pytest.mark.unit
def test_skill_prints_agent_guide_section(tmp_path: Path, monkeypatch, capsys) -> None:
    """``mdwiki skill`` includes an agent-facing how-to guide after the schema."""
    init_wiki(tmp_path, profile="working-dir")
    monkeypatch.chdir(tmp_path)

    rc = main(["skill"])
    out = capsys.readouterr().out

    assert rc == 0
    # The packaged skill router follows the schema.
    assert "Intent router" in out


@pytest.mark.unit
def test_skill_guide_describes_failed_source_status_and_retry(tmp_path: Path, monkeypatch, capsys) -> None:
    init_wiki(tmp_path, profile="working-dir")
    monkeypatch.chdir(tmp_path)

    assert main(["skill"]) == 0
    out = capsys.readouterr().out

    assert "failed sources with reasons" in out
    assert "retries only pending or failed sources" in out


@pytest.mark.unit
def test_skill_guide_defines_safe_native_session_multi_agent_protocol(tmp_path: Path, monkeypatch, capsys) -> None:
    init_wiki(tmp_path, profile="working-dir")
    monkeypatch.chdir(tmp_path)

    assert main(["skill"]) == 0
    out = capsys.readouterr().out

    assert "session-ingest" in out
    assert "exactly one sub-agent" in out
    assert "active Codex or Claude Code session" in out
    assert "never call separate model APIs or local inference" in out
    assert "read-only" in out
    assert "agent-slot limit" in out
    assert "parent" in out.lower() and "apply" in out
    assert "invalidated" in out and "retry" in out


@pytest.mark.unit
def test_skill_emits_initiative_schema_when_initiative_profile_used(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """When the wiki was init'd with --profile=initiative, skill prints THAT schema."""
    init_wiki(tmp_path, profile="initiative")
    monkeypatch.chdir(tmp_path)

    rc = main(["skill"])
    out = capsys.readouterr().out.lower()

    assert rc == 0
    assert "decision" in out
    assert "workstream" in out


@pytest.mark.unit
def test_skill_outside_wiki_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    """``mdwiki skill`` outside a wiki folder exits non-zero with a clear error."""
    monkeypatch.chdir(tmp_path)

    rc = main(["skill"])
    err = capsys.readouterr().err

    assert rc == 1
    assert "wiki" in err.lower()


# --- v1.4.0: packaged mdwiki skill (intent router + per-operation references) ---

SKILL_INTENTS = ("bootstrap", "ingest", "session-ingest", "search", "query", "synthesize", "overview", "lint", "undo")


@pytest.mark.unit
def test_skill_router_maps_every_intent() -> None:
    from mdwiki.skill import SKILL_ROOT, list_workflows

    router = (SKILL_ROOT / "SKILL.md").read_text()
    assert router.startswith("---\nname: mdwiki\n")
    assert "markdown-consolidator" not in router
    for intent in SKILL_INTENTS:
        assert f"references/{intent}.md" in router, f"router does not route {intent}"
        assert intent in list_workflows()


@pytest.mark.unit
def test_skill_prints_reference_workflows(tmp_path: Path, monkeypatch, capsys) -> None:
    init_wiki(tmp_path, profile="working-dir")
    monkeypatch.chdir(tmp_path)

    assert main(["skill"]) == 0
    out = capsys.readouterr().out

    assert "Page kinds" in out  # schema first
    assert "name: mdwiki" not in out  # frontmatter is stripped from the router
    from mdwiki.skill import workflow_text

    for intent in SKILL_INTENTS:
        assert workflow_text(intent) in out
    assert "session-ingest pending" in out
    assert "exactly one sub-agent" in out


@pytest.mark.unit
def test_skill_workflow_flag_prints_single_reference(tmp_path: Path, monkeypatch, capsys) -> None:
    init_wiki(tmp_path, profile="working-dir")
    monkeypatch.chdir(tmp_path)

    assert main(["skill", "--workflow", "lint"]) == 0
    out = capsys.readouterr().out
    assert "mdwiki lint" in out
    assert "Page kinds" not in out

    assert main(["skill", "--workflow", "nope"]) == 2
    assert "unknown workflow" in capsys.readouterr().err


@pytest.mark.unit
def test_skill_root_symlink_matches_package() -> None:
    from mdwiki.skill import SKILL_ROOT

    repo_skill = Path(__file__).resolve().parents[1] / "skill"
    assert repo_skill.is_symlink()
    assert repo_skill.resolve() == SKILL_ROOT.resolve()
    assert not (SKILL_ROOT / "references" / "ALGORITHMS.md").exists()


@pytest.mark.unit
def test_legacy_consolidator_modules_removed() -> None:
    import importlib

    for name in ("consolidator", "clustering", "inventory", "tree_builder", "synthesis", "relationships", "keywords", "summarizer"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(f"mdwiki.{name}")
    import mdwiki

    assert "consolidate" not in mdwiki.__all__

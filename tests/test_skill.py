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
    # The guide section has its own header.
    assert "How to use this wiki" in out


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

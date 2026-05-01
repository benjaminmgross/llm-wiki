"""Tests for ``allowed_kinds_for_wiki`` and ``parse_plan(allowed_kinds=...)``.

The contract: profile-aware ingest unions ``VALID_KINDS`` with each profile's
declared ``extra_page_kinds``, so a ``framework``-profile wiki can persist a
``procedure`` page while a default ``working-dir`` wiki cannot.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mdwiki.init import init_wiki
from mdwiki.plan import VALID_KINDS, PlanValidationError, allowed_kinds_for_wiki, parse_plan


@pytest.mark.unit
def test_allowed_kinds_for_wiki_returns_baseline_when_no_config(tmp_path: Path) -> None:
    """No ``.mdwiki/config.toml`` at all → fall back to the baseline ``VALID_KINDS``."""
    assert allowed_kinds_for_wiki(tmp_path) == VALID_KINDS


@pytest.mark.unit
def test_allowed_kinds_for_wiki_returns_baseline_when_profile_name_absent(tmp_path: Path) -> None:
    """Config exists but doesn't set ``[profile].name`` → baseline."""
    (tmp_path / ".mdwiki").mkdir()
    (tmp_path / ".mdwiki" / "config.toml").write_text("[ingest]\ncandidate_top_k = 8\n")
    assert allowed_kinds_for_wiki(tmp_path) == VALID_KINDS


@pytest.mark.unit
def test_allowed_kinds_for_wiki_unions_framework_extras(tmp_path: Path) -> None:
    """A wiki initialized with ``--profile=framework`` adds ``procedure``/``template``/etc."""
    init_wiki(tmp_path, profile="framework")

    allowed = allowed_kinds_for_wiki(tmp_path)

    assert VALID_KINDS.issubset(allowed)
    assert {"procedure", "template", "assessment", "learning"}.issubset(allowed)


@pytest.mark.unit
def test_allowed_kinds_for_wiki_returns_baseline_for_corrupt_config(tmp_path: Path) -> None:
    """Malformed TOML → baseline rather than crashing the ingest pipeline."""
    (tmp_path / ".mdwiki").mkdir()
    (tmp_path / ".mdwiki" / "config.toml").write_text("this is = not = valid toml [[\n")
    assert allowed_kinds_for_wiki(tmp_path) == VALID_KINDS


@pytest.mark.unit
def test_allowed_kinds_for_wiki_returns_baseline_when_extras_not_a_list(tmp_path: Path) -> None:
    """``extra_page_kinds = "procedure"`` (a string, not a list) → baseline (defensive)."""
    (tmp_path / ".mdwiki").mkdir()
    (tmp_path / ".mdwiki" / "config.toml").write_text(
        '[profile]\nname = "custom"\n\n[profile.custom]\nextra_page_kinds = "procedure"\n'
    )
    assert allowed_kinds_for_wiki(tmp_path) == VALID_KINDS


def _plan_with_kind(kind: str) -> str:
    """Return a minimal valid plan JSON whose only new_page uses ``kind``."""
    return json.dumps(
        {
            "verdict": "ingest",
            "rationale": "single new page.",
            "updates": [],
            "new_pages": [
                {
                    "path": f"wiki/{kind}s/example.md",
                    "kind": kind,
                    "content": f"# Example {kind}\n\nbody.",
                    "claims": [
                        {"source_section_id": "src.md/Intro", "quote": "the cited text matches"}
                    ],
                }
            ],
            "cross_refs": [],
        }
    )


@pytest.mark.unit
def test_parse_plan_accepts_procedure_when_allowed_kinds_includes_it() -> None:
    """``parse_plan`` honors a profile-aware allowed_kinds set."""
    expanded = frozenset(VALID_KINDS | {"procedure"})

    plan = parse_plan(_plan_with_kind("procedure"), allowed_kinds=expanded)

    assert plan.new_pages[0].kind == "procedure"


@pytest.mark.unit
def test_parse_plan_rejects_procedure_with_default_allowed_kinds() -> None:
    """Without the profile's extras, ``procedure`` is not in ``VALID_KINDS`` and is rejected."""
    with pytest.raises(PlanValidationError, match="Invalid page kind"):
        parse_plan(_plan_with_kind("procedure"))

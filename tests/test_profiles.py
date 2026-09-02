"""Tests for ``mdwiki.profiles`` — corpus-aware profile loading.

Phase 1 ships the profile machinery with a single ``working-dir`` profile that
is byte-identical to today's ``DEFAULT_SCHEMA`` — no behavior change.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mdwiki.init import DEFAULT_CONFIG, DEFAULT_SCHEMA, init_wiki
from mdwiki.profiles import (
    UnknownProfileError,
    deep_merge,
    list_profile_names,
    load_profile,
)


@pytest.mark.unit
def test_load_profile_working_dir_returns_default_schema_verbatim() -> None:
    """working-dir profile must be byte-identical to the current DEFAULT_SCHEMA."""
    profile = load_profile(name="working-dir")

    assert profile.name == "working-dir"
    assert profile.schema_text == DEFAULT_SCHEMA
    assert profile.config_overlay == {}


@pytest.mark.unit
def test_load_profile_returns_immutable_profile() -> None:
    """``Profile`` is a frozen dataclass — mutating fields raises ``FrozenInstanceError``."""
    profile = load_profile(name="working-dir")
    with pytest.raises(Exception):
        profile.name = "other"  # type: ignore[misc]


@pytest.mark.unit
def test_load_profile_unknown_raises_unknown_profile_error() -> None:
    """An unknown profile name surfaces as ``UnknownProfileError`` with available names listed."""
    with pytest.raises(UnknownProfileError) as excinfo:
        load_profile(name="nonexistent-profile-xyz")
    assert "nonexistent-profile-xyz" in str(excinfo.value)
    # The error message lists the available profiles; "working-dir" is the
    # always-present default, so it must appear.
    assert "working-dir" in str(excinfo.value)


@pytest.mark.unit
def test_list_profile_names_includes_working_dir() -> None:
    """``working-dir`` is always in the list — it is the default profile."""
    names = list_profile_names()
    assert "working-dir" in names
    # Sorted, no duplicates.
    assert names == sorted(set(names))


@pytest.mark.unit
def test_deep_merge_overlay_wins_on_leaf_conflict() -> None:
    """When base and overlay both define the same scalar key, overlay wins."""
    base = {"a": 1, "b": 2}
    overlay = {"b": 99}

    merged = deep_merge(base=base, overlay=overlay)

    assert merged == {"a": 1, "b": 99}
    # Originals unmutated.
    assert base == {"a": 1, "b": 2}
    assert overlay == {"b": 99}


@pytest.mark.unit
def test_deep_merge_recurses_into_nested_dicts() -> None:
    """Nested dicts merge recursively rather than replacing wholesale."""
    base = {"llm": {"provider": "anthropic", "model": "claude-sonnet-4-6"}}
    overlay = {"llm": {"model": "claude-opus-4-7"}}

    merged = deep_merge(base=base, overlay=overlay)

    assert merged == {"llm": {"provider": "anthropic", "model": "claude-opus-4-7"}}


@pytest.mark.unit
def test_deep_merge_preserves_base_keys_overlay_doesnt_touch() -> None:
    """A key only in base survives an overlay that doesn't mention it."""
    base = {"keep_me": "value", "override_me": "old"}
    overlay = {"override_me": "new"}

    merged = deep_merge(base=base, overlay=overlay)

    assert merged["keep_me"] == "value"
    assert merged["override_me"] == "new"


@pytest.mark.unit
def test_deep_merge_overlay_dict_replaces_base_scalar() -> None:
    """When base has a scalar and overlay has a dict for the same key, overlay wins (dict)."""
    base = {"x": "scalar"}
    overlay = {"x": {"nested": "dict"}}

    merged = deep_merge(base=base, overlay=overlay)

    assert merged == {"x": {"nested": "dict"}}


@pytest.mark.unit
def test_init_with_default_profile_matches_init_without_profile_flag(tmp_path: Path) -> None:
    """``init_wiki(target)`` and ``init_wiki(target, profile="working-dir")`` produce identical .mdwiki/ contents."""
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "doc.md").write_text("# Doc\n\n## Section\n\nbody body body content")
    (b / "doc.md").write_text("# Doc\n\n## Section\n\nbody body body content")

    init_wiki(a)
    init_wiki(b, profile="working-dir")

    assert (a / ".mdwiki" / "schema.md").read_text() == (b / ".mdwiki" / "schema.md").read_text()
    assert (a / ".mdwiki" / "config.toml").read_text() == (b / ".mdwiki" / "config.toml").read_text()


@pytest.mark.unit
def test_init_with_unknown_profile_raises(tmp_path: Path) -> None:
    """``init_wiki(target, profile="nonexistent")`` surfaces ``UnknownProfileError``."""
    (tmp_path / "doc.md").write_text("# Doc\n\nbody")

    with pytest.raises(UnknownProfileError):
        init_wiki(tmp_path, profile="nonexistent-profile")


@pytest.mark.unit
def test_profile_loaded_into_init_writes_schema_text(tmp_path: Path) -> None:
    """The schema_text of the chosen profile lands at .mdwiki/schema.md verbatim."""
    (tmp_path / "doc.md").write_text("# Doc\n\nbody")

    init_wiki(tmp_path, profile="working-dir")

    written_schema = (tmp_path / ".mdwiki" / "schema.md").read_text()
    profile = load_profile(name="working-dir")
    assert written_schema == profile.schema_text


@pytest.mark.unit
def test_profile_overlay_merges_into_default_config(tmp_path: Path) -> None:
    """A profile with a config overlay produces a deep-merged ``config.toml`` (overlay wins)."""
    # We can verify the default working-dir produces DEFAULT_CONFIG verbatim — a
    # later phase will add a profile with an actual overlay and exercise the
    # merge behavior end-to-end.
    (tmp_path / "doc.md").write_text("# Doc\n\nbody")

    init_wiki(tmp_path, profile="working-dir")

    import tomllib

    written_config = tomllib.loads((tmp_path / ".mdwiki" / "config.toml").read_text())
    # ``[pointers].files`` records the runtime pointer files init wrote; it is
    # the only key outside DEFAULT_CONFIG for the working-dir profile.
    assert written_config.pop("pointers") == {"files": ["CLAUDE.md", "AGENTS.md"]}
    assert written_config == DEFAULT_CONFIG


# ---------------------------------------------------------------------------
# Phase 2: initiative profile
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_initiative_profile_loads_with_expected_page_kinds() -> None:
    """The initiative profile schema must declare its workhorse page kinds."""
    profile = load_profile(name="initiative")

    assert profile.name == "initiative"
    schema = profile.schema_text.lower()
    assert "decision" in schema
    assert "status" in schema
    assert "owner" in schema
    assert "workstream" in schema
    assert profile.description  # non-empty


@pytest.mark.unit
def test_initiative_profile_in_list_profile_names() -> None:
    """``initiative`` shows up in ``list_profile_names()``."""
    names = list_profile_names()
    assert "initiative" in names


@pytest.mark.unit
def test_init_with_initiative_profile_writes_initiative_schema(tmp_path: Path) -> None:
    """``init --profile=initiative`` writes the initiative schema, not the default."""
    (tmp_path / "README.md").write_text("# Initiative\n\n## Strategy\n\ncontent here")

    init_wiki(tmp_path, profile="initiative")

    written_schema = (tmp_path / ".mdwiki" / "schema.md").read_text().lower()
    assert "decision" in written_schema
    assert "workstream" in written_schema


# ---------------------------------------------------------------------------
# Phase 3: transcripts profile
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_transcripts_profile_loads_with_expected_page_kinds() -> None:
    """The transcripts profile schema must declare meeting/decision/commitment/blocker."""
    profile = load_profile(name="transcripts")

    schema = profile.schema_text.lower()
    assert "decision" in schema
    assert "commitment" in schema
    assert "blocker" in schema
    assert "meeting" in schema
    assert "person" in schema  # mandatory entity per speaker
    assert profile.description


@pytest.mark.unit
def test_transcripts_profile_in_list_profile_names() -> None:
    """``transcripts`` shows up in ``list_profile_names()``."""
    assert "transcripts" in list_profile_names()


@pytest.mark.unit
def test_init_with_transcripts_profile_writes_transcripts_schema(tmp_path: Path) -> None:
    """``init --profile=transcripts`` writes the transcripts schema."""
    (tmp_path / "doc.md").write_text("# Doc\n\nbody")

    init_wiki(tmp_path, profile="transcripts")

    written_schema = (tmp_path / ".mdwiki" / "schema.md").read_text().lower()
    assert "commitment" in written_schema
    assert "speaker" in written_schema or "person" in written_schema


# ---------------------------------------------------------------------------
# Phase 4: framework profile
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_framework_profile_loads_with_four_kind_taxonomy() -> None:
    """The framework profile schema must declare procedure / template / assessment / learning."""
    profile = load_profile(name="framework")

    schema = profile.schema_text.lower()
    assert "procedure" in schema
    assert "template" in schema
    assert "assessment" in schema
    assert "learning" in schema
    assert profile.description


@pytest.mark.unit
def test_framework_profile_in_list_profile_names() -> None:
    """``framework`` shows up in ``list_profile_names()``."""
    assert "framework" in list_profile_names()


@pytest.mark.unit
def test_init_with_framework_profile_writes_framework_schema(tmp_path: Path) -> None:
    """``init --profile=framework`` writes the four-kind framework schema."""
    (tmp_path / "README.md").write_text("# Framework\n\n## Phase 1\n\ncontent")

    init_wiki(tmp_path, profile="framework")

    written_schema = (tmp_path / ".mdwiki" / "schema.md").read_text().lower()
    assert "procedure" in written_schema
    assert "template" in written_schema
    assert "assessment" in written_schema


# ---------------------------------------------------------------------------
# Phase 2 (continued): initiative profile overlay
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_initiative_profile_has_overlay_with_higher_top_k(tmp_path: Path) -> None:
    """The initiative profile's overlay raises ``candidate_top_k`` above the default."""
    (tmp_path / "README.md").write_text("# Initiative\n\n## Strategy\n\ncontent here")

    init_wiki(tmp_path, profile="initiative")

    import tomllib

    written_config = tomllib.loads((tmp_path / ".mdwiki" / "config.toml").read_text())
    # Default is 8 (DEFAULT_CONFIG['ingest']['candidate_top_k']); initiative
    # raises this because initiative folders typically have more candidate
    # pages worth showing the LLM. The exact value can shift in tuning, but
    # it must be strictly greater than the default to be load-bearing.
    assert written_config["ingest"]["candidate_top_k"] > DEFAULT_CONFIG["ingest"]["candidate_top_k"]


# --- v1.4.0: research profile --------------------------------------------------


@pytest.mark.unit
def test_research_profile_loads_with_paper_claim_method_dataset_kinds(tmp_path: Path) -> None:
    profile = load_profile(name="research")
    assert profile.name == "research"
    assert profile.config_overlay["profile"]["research"]["extra_page_kinds"] == ["paper", "claim", "method", "dataset"]
    for kind in ("paper", "claim", "method", "dataset"):
        assert f"`{kind}`" in profile.schema_text
    assert "research" in list_profile_names()

    from mdwiki.plan import allowed_kinds_for_wiki

    init_wiki(tmp_path, profile="research", pointers=())
    assert {"paper", "claim", "method", "dataset"} <= allowed_kinds_for_wiki(tmp_path)
    assert "language" in (tmp_path / ".mdwiki" / "schema.md").read_text().lower()

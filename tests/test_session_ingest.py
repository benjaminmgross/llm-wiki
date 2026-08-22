"""Tests for native-session, multi-agent corpus ingestion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from mdwiki.init import init_wiki
from mdwiki.lint import lint_wiki
from mdwiki.session_ingest import (
    PlanInvalidatedError,
    apply_session_plan,
    list_session_sources,
    prepare_session_plan,
)
from mdwiki.state import connect
from mdwiki.undo import undo_last


@pytest.fixture
def session_wiki(tmp_path: Path) -> Path:
    for name in ("a", "b"):
        (tmp_path / f"{name}.md").write_text(
            f"# {name}\n\n## Evidence\n\nSource {name} contains durable evidence for the shared knowledge page.\n"
        )
    init_wiki(tmp_path)
    return tmp_path


def _plan_for_update(*, page: str, section_id: str, content: str) -> dict[str, object]:
    return {
        "verdict": "ingest",
        "rationale": "Add the source's durable evidence.",
        "updates": [
            {
                "page": page,
                "content": content,
                "claims": [
                    {
                        "source_section_id": section_id,
                        "quote": f"Source {section_id[0]} contains durable evidence for the shared knowledge page",
                    }
                ],
            }
        ],
        "new_pages": [],
        "cross_refs": [],
    }


def _plan_for_new_page(*, page: str, section_id: str, content: str) -> dict[str, object]:
    return {
        "verdict": "ingest",
        "rationale": "Create a sourced knowledge page.",
        "updates": [],
        "new_pages": [
            {
                "path": page,
                "kind": "concept",
                "content": content,
                "claims": [
                    {
                        "source_section_id": section_id,
                        "quote": f"Source {section_id[0]} contains durable evidence for the shared knowledge page",
                    }
                ],
            }
        ],
        "cross_refs": [],
    }


def _with_plan(envelope: dict[str, object], plan: dict[str, object]) -> dict[str, object]:
    return {**envelope, "plan": plan}


@pytest.mark.unit
def test_session_manifest_assigns_each_pending_source_once_in_stable_order(session_wiki: Path) -> None:
    sources = list_session_sources(session_wiki)

    assert [source.original_path for source in sources] == ["a.md", "b.md"]
    assert len({source.source_id for source in sources}) == len(sources)


@pytest.mark.unit
def test_prepare_session_plan_uses_no_provider_or_embedder(
    session_wiki: Path,
    mocker: MockerFixture,
) -> None:
    provider_factory = mocker.patch("mdwiki.ingest.build_provider_from_config")
    embedder_factory = mocker.patch("mdwiki.ingest.get_default_embedder")
    migrating_connect = mocker.patch("mdwiki.ingest.connect", side_effect=AssertionError("prepare must use a read-only connection"))

    envelope = prepare_session_plan(session_wiki, "a.md").to_dict()

    assert envelope["version"] == 1
    assert envelope["source"]["original_path"] == "a.md"  # type: ignore[index]
    assert envelope["source"]["sections"]  # type: ignore[index]
    assert envelope["snapshot"]["schema_sha256"]  # type: ignore[index]
    assert envelope["plan_contract"]
    assert "fill only the plan field" in envelope["agent_instructions"]  # type: ignore[operator]
    assert envelope["plan"] is None
    provider_factory.assert_not_called()
    embedder_factory.assert_not_called()
    migrating_connect.assert_not_called()


@pytest.mark.unit
def test_apply_session_plan_persists_page_state_and_independent_undo(session_wiki: Path) -> None:
    envelope = prepare_session_plan(session_wiki, "a.md").to_dict()
    payload = _with_plan(
        envelope,
        _plan_for_new_page(
            page="wiki/concepts/a-evidence.md",
            section_id="a.md/Evidence",
            content="# A evidence\n\nSource A evidence.",
        ),
    )

    result = apply_session_plan(session_wiki, payload)

    assert result.applied is True
    assert (session_wiki / "wiki" / "concepts" / "a-evidence.md").is_file()
    with connect(session_wiki / ".mdwiki" / "state.db") as conn:
        source = conn.execute("SELECT status FROM sources WHERE original_path = 'a.md'").fetchone()
        transactions = conn.execute("SELECT COUNT(*) AS count FROM transactions WHERE applied = 1").fetchone()
    assert source["status"] == "ingested"
    assert transactions["count"] == 1

    undo_last(session_wiki)

    assert not (session_wiki / "wiki" / "concepts" / "a-evidence.md").exists()
    with connect(session_wiki / ".mdwiki" / "state.db") as conn:
        source = conn.execute("SELECT status FROM sources WHERE original_path = 'a.md'").fetchone()
    assert source["status"] == "pending"


@pytest.mark.unit
def test_overlapping_plan_is_invalidated_then_fresh_retry_preserves_prior_update(session_wiki: Path) -> None:
    page = session_wiki / "wiki" / "concepts" / "shared.md"
    page.parent.mkdir(parents=True)
    page.write_text("# Shared\n\nInitial knowledge.\n")
    a_envelope = prepare_session_plan(session_wiki, "a.md").to_dict()
    b_stale_envelope = prepare_session_plan(session_wiki, "b.md").to_dict()

    apply_session_plan(
        session_wiki,
        _with_plan(
            a_envelope,
            _plan_for_update(
                page="wiki/concepts/shared.md",
                section_id="a.md/Evidence",
                content="# Shared\n\nInitial knowledge.\n\nSource A evidence.\n",
            ),
        ),
    )

    with pytest.raises(PlanInvalidatedError, match="wiki/concepts/shared.md"):
        apply_session_plan(
            session_wiki,
            _with_plan(
                b_stale_envelope,
                _plan_for_update(
                    page="wiki/concepts/shared.md",
                    section_id="b.md/Evidence",
                    content="# Shared\n\nInitial knowledge.\n\nSource B evidence.\n",
                ),
            ),
        )
    assert "Source A evidence" in page.read_text()
    assert "Source B evidence" not in page.read_text()

    b_fresh_envelope = prepare_session_plan(session_wiki, "b.md").to_dict()
    apply_session_plan(
        session_wiki,
        _with_plan(
            b_fresh_envelope,
            _plan_for_update(
                page="wiki/concepts/shared.md",
                section_id="b.md/Evidence",
                content="# Shared\n\nInitial knowledge.\n\nSource A evidence.\n\nSource B evidence.\n",
            ),
        ),
    )

    assert "Source A evidence" in page.read_text()
    assert "Source B evidence" in page.read_text()


@pytest.mark.unit
def test_disjoint_plan_remains_valid_after_unrelated_page_changes(session_wiki: Path) -> None:
    for name in ("a", "b"):
        page = session_wiki / "wiki" / "concepts" / f"{name}.md"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(f"# {name}\n\nInitial {name}.\n")
    a_envelope = prepare_session_plan(session_wiki, "a.md").to_dict()
    b_envelope = prepare_session_plan(session_wiki, "b.md").to_dict()

    apply_session_plan(
        session_wiki,
        _with_plan(
            a_envelope,
            _plan_for_update(
                page="wiki/concepts/a.md",
                section_id="a.md/Evidence",
                content="# a\n\nInitial a.\n\nSource A evidence.\n",
            ),
        ),
    )
    result = apply_session_plan(
        session_wiki,
        _with_plan(
            b_envelope,
            _plan_for_update(
                page="wiki/concepts/b.md",
                section_id="b.md/Evidence",
                content="# b\n\nInitial b.\n\nSource B evidence.\n",
            ),
        ),
    )

    assert result.applied is True
    assert "Source A evidence" in (session_wiki / "wiki" / "concepts" / "a.md").read_text()
    assert "Source B evidence" in (session_wiki / "wiki" / "concepts" / "b.md").read_text()


@pytest.mark.unit
def test_apply_session_plan_rejects_source_content_changed_since_prepare(session_wiki: Path) -> None:
    envelope = prepare_session_plan(session_wiki, "a.md").to_dict()
    raw_path = session_wiki / envelope["source"]["raw_path"]  # type: ignore[index,operator]
    raw_path.write_text("changed after analysis")

    with pytest.raises(PlanInvalidatedError, match="source changed"):
        apply_session_plan(
            session_wiki,
            _with_plan(
                envelope,
                {
                    "verdict": "low-quality",
                    "rationale": "No useful content.",
                    "updates": [],
                    "new_pages": [],
                    "cross_refs": [],
                },
            ),
        )


@pytest.mark.unit
def test_apply_session_plan_invalidates_when_config_policy_changed_since_prepare(session_wiki: Path) -> None:
    envelope = prepare_session_plan(session_wiki, "a.md").to_dict()
    config_path = session_wiki / ".mdwiki" / "config.toml"
    config_path.write_text(config_path.read_text().replace("candidate_top_k = 8", "candidate_top_k = 8\nmin_quote_words = 11"))

    with pytest.raises(PlanInvalidatedError, match="configuration changed"):
        apply_session_plan(
            session_wiki,
            _with_plan(
                envelope,
                _plan_for_new_page(
                    page="wiki/concepts/a-evidence.md",
                    section_id="a.md/Evidence",
                    content="# A evidence\n\nSource A evidence.",
                ),
            ),
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("relative_path", "message"),
    [
        (".mdwiki/schema.md", "schema disappeared"),
        ("raw/{raw_name}", "source disappeared"),
    ],
)
def test_apply_session_plan_maps_deleted_snapshot_input_to_invalidation(
    session_wiki: Path,
    relative_path: str,
    message: str,
) -> None:
    envelope = prepare_session_plan(session_wiki, "a.md").to_dict()
    raw_name = Path(envelope["source"]["raw_path"]).name  # type: ignore[index,arg-type]
    (session_wiki / relative_path.format(raw_name=raw_name)).unlink()

    with pytest.raises(PlanInvalidatedError, match=message):
        apply_session_plan(
            session_wiki,
            _with_plan(
                envelope,
                {
                    "verdict": "low-quality",
                    "rationale": "No useful content.",
                    "updates": [],
                    "new_pages": [],
                    "cross_refs": [],
                },
            ),
        )


@pytest.mark.unit
def test_prepared_envelope_round_trips_as_json(session_wiki: Path) -> None:
    encoded = prepare_session_plan(session_wiki, "a.md").to_json()

    assert json.loads(encoded)["source"]["original_path"] == "a.md"


@pytest.mark.unit
def test_same_source_envelope_cannot_be_applied_twice(session_wiki: Path) -> None:
    envelope = prepare_session_plan(session_wiki, "a.md").to_dict()
    payload = _with_plan(
        envelope,
        {
            "verdict": "low-quality",
            "rationale": "No additional load-bearing content.",
            "updates": [],
            "new_pages": [],
            "cross_refs": [],
        },
    )
    apply_session_plan(session_wiki, payload)

    with pytest.raises(PlanInvalidatedError, match="already ingested"):
        apply_session_plan(session_wiki, payload)


@pytest.mark.unit
def test_session_cross_ref_materializes_link_visible_to_lint(session_wiki: Path) -> None:
    target = session_wiki / "wiki" / "concepts" / "target.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Target\n\nExisting target.\n")
    envelope = prepare_session_plan(session_wiki, "a.md").to_dict()
    plan = _plan_for_new_page(
        page="wiki/concepts/source.md",
        section_id="a.md/Evidence",
        content="# Source\n\nSource A evidence.\n",
    )
    plan["cross_refs"] = [
        {
            "from_page": "wiki/concepts/source.md",
            "to_page": "wiki/concepts/target.md",
            "anchor_text": "Existing target",
        }
    ]

    apply_session_plan(session_wiki, _with_plan(envelope, plan))

    assert "[Existing target](target.md)" in (session_wiki / "wiki" / "concepts" / "source.md").read_text()
    orphan_paths = {finding.page_path for finding in lint_wiki(session_wiki).findings if finding.kind == "orphan"}
    assert "wiki/concepts/target.md" not in orphan_paths


@pytest.mark.unit
def test_session_cross_ref_invalidates_when_existing_from_page_changed(session_wiki: Path) -> None:
    source = session_wiki / "wiki" / "concepts" / "source.md"
    target = session_wiki / "wiki" / "concepts" / "target.md"
    source.parent.mkdir(parents=True)
    source.write_text("# Source\n\nOriginal.\n")
    target.write_text("# Target\n\nOriginal.\n")
    envelope = prepare_session_plan(session_wiki, "a.md").to_dict()
    source.write_text("# Source\n\nChanged concurrently.\n")
    plan = _plan_for_new_page(
        page="wiki/concepts/evidence.md",
        section_id="a.md/Evidence",
        content="# Evidence\n\nSource A evidence.\n",
    )
    plan["cross_refs"] = [
        {
            "from_page": "wiki/concepts/source.md",
            "to_page": "wiki/concepts/target.md",
            "anchor_text": "Target",
        }
    ]

    with pytest.raises(PlanInvalidatedError, match="source.md"):
        apply_session_plan(session_wiki, _with_plan(envelope, plan))


@pytest.mark.unit
def test_session_cross_ref_accepts_target_only_content_change(session_wiki: Path) -> None:
    source = session_wiki / "wiki" / "concepts" / "source.md"
    target = session_wiki / "wiki" / "concepts" / "target.md"
    source.parent.mkdir(parents=True)
    source.write_text("# Source\n\nOriginal.\n")
    target.write_text("# Target\n\nOriginal.\n")
    envelope = prepare_session_plan(session_wiki, "a.md").to_dict()
    target.write_text("# Target\n\nChanged concurrently.\n")
    plan = _plan_for_new_page(page="wiki/concepts/evidence.md", section_id="a.md/Evidence", content="# Evidence\n\nSource A evidence.\n")
    plan["cross_refs"] = [{"from_page": "wiki/concepts/source.md", "to_page": "wiki/concepts/target.md", "anchor_text": "Target"}]

    apply_session_plan(session_wiki, _with_plan(envelope, plan))

    assert "[Target](target.md)" in source.read_text()


@pytest.mark.unit
def test_session_cross_ref_rejects_deleted_target_only_page(session_wiki: Path) -> None:
    source = session_wiki / "wiki" / "concepts" / "source.md"
    target = session_wiki / "wiki" / "concepts" / "target.md"
    source.parent.mkdir(parents=True)
    source.write_text("# Source\n")
    target.write_text("# Target\n")
    envelope = prepare_session_plan(session_wiki, "a.md").to_dict()
    target.unlink()
    plan = _plan_for_new_page(page="wiki/concepts/evidence.md", section_id="a.md/Evidence", content="# Evidence\n\nSource A evidence.\n")
    plan["cross_refs"] = [{"from_page": "wiki/concepts/source.md", "to_page": "wiki/concepts/target.md", "anchor_text": "Target"}]

    with pytest.raises(PlanInvalidatedError, match="target.md"):
        apply_session_plan(session_wiki, _with_plan(envelope, plan))

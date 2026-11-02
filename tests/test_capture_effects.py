"""Capture projections through real native-session apply and undo."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from mdwiki.capture import CaptureRecord
from mdwiki.ingest import IngestResult
from mdwiki.init import init_wiki
from mdwiki.session_ingest import apply_session_plan, prepare_session_plan
from mdwiki.state import connect
from mdwiki.undo import undo_last


def _capture(wiki: Path, *, title: str = "No obsolete tier", supersedes: tuple[str, ...] = ()) -> CaptureRecord:
    from mdwiki.capture import CaptureInput, register_capture

    return register_capture(
        wiki_root=wiki,
        capture=CaptureInput(
            kind="standing-rule",
            title=title,
            body="The obsolete tier must never be used.",
            decided_by="Ben",
            supersedes=supersedes,
        ),
    )


def _apply(wiki: Path, record: CaptureRecord, *, page: str = "wiki/concepts/rule.md") -> IngestResult:
    envelope = prepare_session_plan(wiki, record.source_id).to_dict()
    envelope["plan"] = {
        "verdict": "ingest",
        "rationale": "Record the standing rule.",
        "updates": [],
        "new_pages": [
            {
                "path": page,
                "kind": "concept",
                "content": f"# {record.capture.title}\n\nThe obsolete tier must never be used.\n",
                "claims": [{"source_section_id": record.original_path + "/Conclusion", "quote": "The obsolete tier must never be used."}],
            }
        ],
        "cross_refs": [],
    }
    return apply_session_plan(wiki, envelope)


def test_session_capture_applies_pins_supersession_and_complete_undo(tmp_path: Path, mocker: MockerFixture) -> None:
    init_wiki(tmp_path)
    old = tmp_path / "wiki/concepts/old.md"
    old.parent.mkdir(parents=True)
    old.write_text("# Old\n\nUse the obsolete tier.\n")
    record = _capture(tmp_path, supersedes=("wiki/concepts/old.md",))
    mocker.patch("mdwiki.ingest.build_provider_from_config", side_effect=AssertionError("no provider"))
    mocker.patch("mdwiki.ingest.get_default_embedder", side_effect=AssertionError("no inference"))
    assert _apply(tmp_path, record).applied
    page = tmp_path / "wiki/concepts/rule.md"
    assert "pinned: true" in page.read_text()
    assert "**Superseded (" in old.read_text()
    assert "The obsolete tier must never be used." in (tmp_path / "wiki/standing-rules.md").read_text()
    assert "Standing rules" in (tmp_path / "wiki/index.md").read_text()
    with connect(tmp_path / ".mdwiki/state.db") as conn:
        assert conn.execute("SELECT pinned FROM pages WHERE path = ?", ("wiki/concepts/rule.md",)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM supersessions").fetchone()[0] == 1
    undo_last(tmp_path)
    assert not page.exists()
    assert old.read_text() == "# Old\n\nUse the obsolete tier.\n"


def test_retirement_preserves_raw_and_undo_preserves_later_registration(tmp_path: Path) -> None:
    from mdwiki.capture import get_capture
    from mdwiki.capture_effects import retire_capture

    init_wiki(tmp_path)
    record = _capture(tmp_path)
    _apply(tmp_path, record)
    raw = (tmp_path / record.raw_path).read_bytes()
    retire_capture(wiki_root=tmp_path, source_id=record.source_id, reason="Policy changed")
    assert "pinned: false" in (tmp_path / "wiki/concepts/rule.md").read_text()
    assert record.capture.title not in (tmp_path / "wiki/standing-rules.md").read_text()
    retired = get_capture(wiki_root=tmp_path, source_id=record.source_id).capture.retired
    assert retired is not None and retired["reason"] == "Policy changed"
    later = _capture(tmp_path, title="Later registration")
    undo_last(tmp_path)
    assert get_capture(wiki_root=tmp_path, source_id=record.source_id).capture.retired is None
    assert get_capture(wiki_root=tmp_path, source_id=later.source_id)
    assert "pinned: true" in (tmp_path / "wiki/concepts/rule.md").read_text()
    assert (tmp_path / record.raw_path).read_bytes() == raw


def test_superseding_capture_replaces_old_pin_and_retire_does_not_revive_it(tmp_path: Path) -> None:
    from mdwiki.capture_effects import retire_capture

    init_wiki(tmp_path)
    first = _capture(tmp_path)
    _apply(tmp_path, first)
    second = _capture(tmp_path, title="Replacement rule", supersedes=(first.source_id,))
    _apply(tmp_path, second, page="wiki/concepts/replacement.md")
    assert "pinned: false" in (tmp_path / "wiki/concepts/rule.md").read_text()
    rules = (tmp_path / "wiki/standing-rules.md").read_text()
    assert "Replacement rule" in rules and "## No obsolete tier" not in rules
    retire_capture(wiki_root=tmp_path, source_id=second.source_id, reason="Retired")
    assert "pinned: true" not in (tmp_path / "wiki/concepts/rule.md").read_text()


def test_rebuild_restores_capture_edges_and_page_pins(tmp_path: Path, mocker: MockerFixture) -> None:
    from mdwiki.page_index import rebuild_page_index
    from mdwiki.rebuild import rebuild_wiki

    init_wiki(tmp_path)
    record = _capture(tmp_path)
    _apply(tmp_path, record)
    (tmp_path / ".mdwiki/state.db").unlink()
    rebuild_wiki(tmp_path)
    embedder = mocker.Mock()
    embedder.embed_text.return_value = [1.0, 0.0]
    rebuild_page_index(tmp_path, embedder=embedder)
    with connect(tmp_path / ".mdwiki/state.db") as conn:
        assert conn.execute("SELECT status FROM sources WHERE id = ?", (record.source_id,)).fetchone()[0] == "ingested"
        assert conn.execute("SELECT pinned FROM pages").fetchone()[0] == 1
    sidecar = json.loads((tmp_path / "raw/.sources.json").read_text())
    assert sidecar[record.source_id]["origin"] == "capture"


def test_reserved_generated_page_cannot_be_written_by_plan() -> None:
    from mdwiki.plan import PlanValidationError, parse_plan_dict

    with pytest.raises(PlanValidationError, match="generated|infrastructure"):
        parse_plan_dict(
            {
                "verdict": "ingest",
                "rationale": "",
                "updates": [{"page": "wiki/standing-rules.md", "content": "bad", "claims": []}],
                "new_pages": [],
                "cross_refs": [],
            }
        )


def test_supersession_reapplies_after_rebuild_and_model_rewrite(tmp_path: Path) -> None:
    from mdwiki.capture_effects import apply_capture_effects
    from mdwiki.rebuild import rebuild_wiki
    from mdwiki.transaction import IngestTransaction

    init_wiki(tmp_path)
    old = tmp_path / "wiki/concepts/old.md"
    old.parent.mkdir(parents=True)
    old.write_text("# Old\n\nOld policy.\n")
    first = _capture(tmp_path)
    _apply(tmp_path, first, page="wiki/concepts/first.md")
    second = _capture(tmp_path, title="New policy", supersedes=(first.source_id,))
    _apply(tmp_path, second, page="wiki/concepts/second.md")
    # Remove just the visible callout: affected page metadata remains durable.
    first_page = tmp_path / "wiki/concepts/first.md"
    content = first_page.read_text()
    content = "\n".join(line for line in content.splitlines() if not line.startswith(("> **Superseded", "<!-- mdwiki-superseded")))
    first_page.write_text(content)
    (tmp_path / ".mdwiki/state.db").unlink()
    rebuild_wiki(tmp_path)
    with IngestTransaction(wiki_root=tmp_path, source_id=None, summary="repair projections") as tx:
        apply_capture_effects(wiki_root=tmp_path, tx=tx, source_id=second.source_id)
    assert "**Superseded (" in first_page.read_text()


def test_source_supersession_skips_mixed_live_source_page(tmp_path: Path) -> None:
    from mdwiki.capture_effects import apply_capture_effects
    from mdwiki.transaction import IngestTransaction

    (tmp_path / "external.md").write_text("# External\n\nOther durable source evidence is still applicable.\n")
    init_wiki(tmp_path)
    first = _capture(tmp_path)
    _apply(tmp_path, first)
    with connect(tmp_path / ".mdwiki/state.db") as conn:
        external = conn.execute("SELECT id FROM sources WHERE original_path='external.md'").fetchone()[0]
        conn.execute(
            "INSERT INTO backrefs(page_path,source_id,quote) VALUES ('wiki/concepts/rule.md',?,'Other durable source evidence')",
            (external,),
        )
    second = _capture(tmp_path, title="New policy", supersedes=(first.source_id,))
    result = _apply(tmp_path, second, page="wiki/concepts/second.md")
    assert "Manual review" in result.message
    assert "**Superseded (" not in (tmp_path / "wiki/concepts/rule.md").read_text()
    with IngestTransaction(wiki_root=tmp_path, source_id=None, summary="idempotent") as tx:
        apply_capture_effects(wiki_root=tmp_path, tx=tx, source_id=second.source_id)
    assert (tmp_path / "wiki/concepts/second.md").read_text().count("pinned: true") == 1


def test_lifecycle_mirror_holds_registration_lock(tmp_path: Path, mocker: MockerFixture) -> None:
    import sqlite3

    from mdwiki.capture_effects import mirror_capture_state
    from mdwiki.init import _write_registration_sidecar

    init_wiki(tmp_path)
    _capture(tmp_path)

    def checked_write(path, sidecar) -> None:
        with sqlite3.connect(tmp_path / ".mdwiki/write.lock", timeout=0) as contender:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                contender.execute("BEGIN IMMEDIATE")
        _write_registration_sidecar(path, sidecar)

    mocker.patch("mdwiki.capture_effects._write_registration_sidecar", side_effect=checked_write)
    mirror_capture_state(wiki_root=tmp_path)


def test_rebuilt_page_keeps_managed_history_when_session_rewrites_body(tmp_path: Path, mocker: MockerFixture) -> None:
    from mdwiki.page_index import rebuild_page_index
    from mdwiki.rebuild import rebuild_wiki

    init_wiki(tmp_path)
    first = _capture(tmp_path)
    _apply(tmp_path, first)
    second = _capture(tmp_path, title="New policy", supersedes=("wiki/concepts/rule.md",))
    _apply(tmp_path, second, page="wiki/concepts/second.md")
    (tmp_path / ".mdwiki/state.db").unlink()
    rebuild_wiki(tmp_path)
    embedder = mocker.Mock()
    embedder.embed_text.return_value = [1.0, 0.0]
    rebuild_page_index(tmp_path, embedder=embedder)
    from mdwiki.capture import CaptureInput, register_capture

    fact = register_capture(
        wiki_root=tmp_path,
        capture=CaptureInput(kind="fact", title="Additional evidence", body="This page needs additional historical context."),
    )
    envelope = prepare_session_plan(tmp_path, fact.source_id).to_dict()
    envelope["plan"] = {
        "verdict": "ingest",
        "rationale": "Add context",
        "new_pages": [],
        "cross_refs": [],
        "updates": [
            {
                "page": "wiki/concepts/rule.md",
                "content": "# Historical rule\n\nThis page needs additional historical context.",
                "claims": [{"source_section_id": fact.original_path + "/Conclusion", "quote": fact.body}],
            }
        ],
    }
    apply_session_plan(tmp_path, envelope)
    assert "**Superseded (" in (tmp_path / "wiki/concepts/rule.md").read_text()
    assert "pinned: false" in (tmp_path / "wiki/concepts/rule.md").read_text()


def test_empty_plan_cannot_silently_discard_pinned_capture(tmp_path: Path) -> None:
    from mdwiki.capture import CaptureError, get_capture

    init_wiki(tmp_path)
    record = _capture(tmp_path)
    envelope = prepare_session_plan(tmp_path, record.source_id).to_dict()
    envelope["plan"] = {"verdict": "low-quality", "rationale": "No updates", "updates": [], "new_pages": [], "cross_refs": []}
    with pytest.raises(CaptureError, match="cited page"):
        apply_session_plan(tmp_path, envelope)
    assert get_capture(wiki_root=tmp_path, source_id=record.source_id).status == "pending"


def test_retired_pending_capture_is_not_sent_to_provider(tmp_path: Path, mocker: MockerFixture) -> None:
    from mdwiki.capture_effects import retire_capture
    from mdwiki.ingest import IngestError, ingest_many, ingest_source
    from mdwiki.session_ingest import list_session_sources

    init_wiki(tmp_path)
    record = _capture(tmp_path)
    retire_capture(wiki_root=tmp_path, source_id=record.source_id, reason="Never adopted")
    provider = mocker.patch("mdwiki.ingest.build_provider_from_config", side_effect=AssertionError("no provider"))
    embedder = mocker.patch("mdwiki.ingest.get_default_embedder", side_effect=AssertionError("no inference"))
    assert list_session_sources(tmp_path) == []
    assert ingest_many(tmp_path) == []
    with pytest.raises(IngestError, match="retired"):
        ingest_source(tmp_path, record.source_id, yes=True)
    provider.assert_not_called()
    embedder.assert_not_called()


def test_page_supersession_keeps_replacement_rule_pinned_on_same_updated_page(tmp_path: Path) -> None:
    init_wiki(tmp_path)
    first = _capture(tmp_path)
    _apply(tmp_path, first)
    replacement = _capture(tmp_path, title="Replacement on same page", supersedes=("wiki/concepts/rule.md",))
    envelope = prepare_session_plan(tmp_path, replacement.source_id).to_dict()
    envelope["plan"] = {
        "verdict": "ingest",
        "rationale": "Replace rule in place",
        "new_pages": [],
        "cross_refs": [],
        "updates": [
            {
                "page": "wiki/concepts/rule.md",
                "content": "# Replacement on same page\n\nThe obsolete tier must never be used.",
                "claims": [{"source_section_id": replacement.original_path + "/Conclusion", "quote": replacement.body}],
            }
        ],
    }
    apply_session_plan(tmp_path, envelope)
    assert "pinned: true" in (tmp_path / "wiki/concepts/rule.md").read_text()
    rules = (tmp_path / "wiki/standing-rules.md").read_text()
    assert "## Replacement on same page" in rules
    assert "## No obsolete tier" not in rules
    undo_last(tmp_path)
    assert "## No obsolete tier" in (tmp_path / "wiki/standing-rules.md").read_text()


def test_source_supersession_is_removed_when_page_gains_other_live_evidence(tmp_path: Path) -> None:
    from mdwiki.capture import CaptureInput, register_capture
    from mdwiki.capture_effects import page_metadata

    init_wiki(tmp_path)
    first = _capture(tmp_path)
    _apply(tmp_path, first)
    replacement = _capture(tmp_path, title="Replacement", supersedes=(first.source_id,))
    _apply(tmp_path, replacement, page="wiki/concepts/replacement.md")
    page = tmp_path / "wiki/concepts/rule.md"
    assert "**Superseded (" in page.read_text()
    fact = register_capture(
        wiki_root=tmp_path, capture=CaptureInput(kind="fact", title="Additional fact", body="Other live evidence applies to this page.")
    )
    envelope = prepare_session_plan(tmp_path, fact.source_id).to_dict()
    envelope["plan"] = {
        "verdict": "ingest",
        "rationale": "Add evidence",
        "new_pages": [],
        "cross_refs": [],
        "updates": [
            {
                "page": "wiki/concepts/rule.md",
                "content": "# Mixed evidence\n\nOther live evidence applies to this page.",
                "claims": [{"source_section_id": fact.original_path + "/Conclusion", "quote": fact.body}],
            }
        ],
    }
    result = apply_session_plan(tmp_path, envelope)
    assert "Manual review" in result.message
    assert "**Superseded (" not in page.read_text()
    superseded_by = page_metadata(content=page.read_text())["superseded_by"]
    assert isinstance(superseded_by, list)
    assert replacement.source_id not in superseded_by

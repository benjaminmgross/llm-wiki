"""Capture storage remains durable, internal, immutable, and safe to export."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from pytest_mock import MockerFixture

from mdwiki.init import SidecarCorruptError, init_wiki
from mdwiki.refresh import refresh_wiki
from mdwiki.state import connect

if TYPE_CHECKING:
    from mdwiki.capture import CaptureKind


@pytest.fixture
def capture_wiki(tmp_path: Path) -> Path:
    init_wiki(tmp_path)
    return tmp_path


def test_registration_creates_only_internal_pending_source(capture_wiki: Path) -> None:
    from mdwiki.capture import CaptureInput, register_capture

    record = register_capture(
        wiki_root=capture_wiki, capture=CaptureInput(kind="fact", title="T", body="A verified conclusion is recorded.")
    )

    raw = capture_wiki / record.raw_path
    assert raw.name == f"{record.source_id}-t.md"
    assert hashlib.sha256(raw.read_bytes()).hexdigest()[:12] == record.source_id
    assert raw.read_text().startswith("# T\n\n## Conclusion\nA verified conclusion is recorded.")
    assert "## Provenance\nKind: fact." in raw.read_text()
    assert record.original_path.startswith("capture://")
    with connect(capture_wiki / ".mdwiki/state.db") as conn:
        row = conn.execute("SELECT * FROM sources").fetchone()
    assert row["status"] == "pending"
    metadata = json.loads((capture_wiki / "raw/.sources.json").read_text())[record.source_id]
    assert metadata["origin"] == metadata["loader"] == "capture"
    assert sorted(path.name for path in capture_wiki.iterdir()) == [".mdwiki", "raw", "wiki"]
    assert list((capture_wiki / "wiki").iterdir()) == []


def test_identical_conclusions_have_distinct_ids(capture_wiki: Path) -> None:
    from mdwiki.capture import CaptureInput, register_capture

    value = CaptureInput(kind="fact", title="Same", body="The conclusion remains the same.")
    first = register_capture(wiki_root=capture_wiki, capture=value)
    second = register_capture(wiki_root=capture_wiki, capture=value)

    assert first.source_id != second.source_id
    assert first.original_path != second.original_path
    assert len(list((capture_wiki / "raw").glob("*.md"))) == 2


@pytest.mark.parametrize("changes", [{"title": " "}, {"body": ""}, {"title": "bad\nheading"}, {"date": "2026-02-30"}, {"date": "20261102"}])
def test_invalid_input_leaves_no_capture(capture_wiki: Path, changes: dict[str, str]) -> None:
    from mdwiki.capture import CaptureError, CaptureInput, register_capture

    with pytest.raises(CaptureError):
        register_capture(
            wiki_root=capture_wiki,
            capture=CaptureInput(
                kind="fact", title=changes.get("title", "T"), body=changes.get("body", "B"), date=changes.get("date", "2026-11-02")
            ),
        )
    assert list((capture_wiki / "raw").glob("*.md")) == []


@pytest.mark.parametrize("kind", ["standing-rule", "decision"])
def test_rules_and_decisions_require_authority(capture_wiki: Path, kind: CaptureKind) -> None:
    from mdwiki.capture import CaptureError, CaptureInput, register_capture

    with pytest.raises(CaptureError, match="decided.by"):
        register_capture(wiki_root=capture_wiki, capture=CaptureInput(kind=kind, title="T", body="B"))


def test_registration_waits_for_lock_before_touching_raw(capture_wiki: Path, mocker: MockerFixture) -> None:
    from mdwiki.capture import CaptureInput, register_capture

    mocker.patch("mdwiki.capture._acquire_write_lock", side_effect=sqlite3.OperationalError("database is locked"))
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        register_capture(wiki_root=capture_wiki, capture=CaptureInput(kind="fact", title="T", body="B"))
    assert list((capture_wiki / "raw").iterdir()) == []


@pytest.mark.parametrize("content", ["{bad", "[]", '{"bad": []}'])
def test_corrupt_sidecar_is_preserved(capture_wiki: Path, content: str) -> None:
    from mdwiki.capture import CaptureInput, register_capture

    sidecar = capture_wiki / "raw/.sources.json"
    sidecar.write_text(content)
    with pytest.raises(SidecarCorruptError):
        register_capture(wiki_root=capture_wiki, capture=CaptureInput(kind="fact", title="T", body="B"))
    assert sidecar.read_text() == content
    assert list((capture_wiki / "raw").glob("*.md")) == []


def test_failed_sidecar_write_rolls_back_source_and_raw(capture_wiki: Path, mocker: MockerFixture) -> None:
    from mdwiki.capture import CaptureInput, register_capture

    mocker.patch("mdwiki.capture._write_registration_sidecar", side_effect=OSError("disk full"))
    with pytest.raises(OSError, match="disk full"):
        register_capture(wiki_root=capture_wiki, capture=CaptureInput(kind="fact", title="T", body="B"))
    with connect(capture_wiki / ".mdwiki/state.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0
    assert list((capture_wiki / "raw").glob("*.md")) == []


def test_export_is_skipped_by_refresh_and_reports_drift(capture_wiki: Path) -> None:
    from mdwiki.capture import CaptureInput, capture_export_state, export_capture, get_capture, register_capture

    record = register_capture(
        wiki_root=capture_wiki, capture=CaptureInput(kind="fact", title="Export", body="A captured conclusion has provenance.")
    )
    assert capture_export_state(wiki_root=capture_wiki, record=record) == "not-exported"
    destination = export_capture(wiki_root=capture_wiki, source_id=record.source_id, path=Path("out.md"))
    assert destination.read_text() == f"<!-- mdwiki-capture: {record.source_id} -->\n{record.raw_text}"
    assert refresh_wiki(capture_wiki).files_registered == 0
    current = get_capture(wiki_root=capture_wiki, source_id=record.source_id[:6])
    assert capture_export_state(wiki_root=capture_wiki, record=current) == "current"
    export_capture(wiki_root=capture_wiki, source_id=record.source_id, path=destination)
    destination.write_text(destination.read_text() + "changed")
    assert capture_export_state(wiki_root=capture_wiki, record=current) == "out-of-date"
    destination.unlink()
    assert capture_export_state(wiki_root=capture_wiki, record=current) == "missing"


@pytest.mark.parametrize("destination", ["raw/new.md", ".mdwiki/new.md", "wiki/new.md", "existing.md"])
def test_export_never_overwrites_sources_or_internal_files(capture_wiki: Path, destination: str) -> None:
    from mdwiki.capture import CaptureError, CaptureInput, export_capture, register_capture

    existing = capture_wiki / "existing.md"
    existing.write_text("existing source")
    record = register_capture(wiki_root=capture_wiki, capture=CaptureInput(kind="fact", title="Export", body="Body"))
    before = (capture_wiki / record.raw_path).read_bytes()
    with pytest.raises(CaptureError):
        export_capture(wiki_root=capture_wiki, source_id=record.source_id, path=Path(destination))
    assert existing.read_text() == "existing source"
    assert (capture_wiki / record.raw_path).read_bytes() == before


def test_export_rejects_symlink_into_raw(capture_wiki: Path) -> None:
    from mdwiki.capture import CaptureError, CaptureInput, export_capture, register_capture

    (capture_wiki / "alias").symlink_to(capture_wiki / "raw", target_is_directory=True)
    record = register_capture(wiki_root=capture_wiki, capture=CaptureInput(kind="fact", title="Export", body="Body"))
    with pytest.raises(CaptureError):
        export_capture(wiki_root=capture_wiki, source_id=record.source_id, path=Path("alias/new.md"))


def test_supersession_targets_are_canonical(capture_wiki: Path) -> None:
    from mdwiki.capture import CaptureInput, register_capture

    page = capture_wiki / "wiki/concepts/old.md"
    page.parent.mkdir()
    page.write_text("# Older page\n")
    old = register_capture(wiki_root=capture_wiki, capture=CaptureInput(kind="fact", title="Old", body="Older body"))
    new = register_capture(
        wiki_root=capture_wiki,
        capture=CaptureInput(kind="correction", title="New", body="New body", supersedes=("wiki/concepts/old.md", old.source_id[:7])),
    )
    assert new.capture.supersedes == ("wiki/concepts/old.md", old.source_id)


@pytest.mark.parametrize(
    "target",
    ["../outside.md", "wiki/../raw/source.md", "wiki/index.md", "wiki/standing-rules.md", "wiki/concepts/missing.md", "unknown-id"],
)
def test_invalid_targets_leave_raw_unchanged(capture_wiki: Path, target: str) -> None:
    from mdwiki.capture import CaptureError, CaptureInput, register_capture

    with pytest.raises(CaptureError):
        register_capture(wiki_root=capture_wiki, capture=CaptureInput(kind="correction", title="T", body="B", supersedes=(target,)))
    assert list((capture_wiki / "raw").glob("*.md")) == []


def test_loaded_capture_preserves_body_with_section_headings(capture_wiki: Path) -> None:
    from mdwiki.capture import CaptureInput, get_capture, load_captures, register_capture

    body = "Conclusion text.\n\n## Provenance\nThis heading belongs to the conclusion.\n\n"
    record = register_capture(
        wiki_root=capture_wiki,
        capture=CaptureInput(kind="standing-rule", title="Rule", body=body, decided_by="Ben", rationale="Why this rule exists."),
    )
    loaded = get_capture(wiki_root=capture_wiki, source_id=record.source_id)
    assert loaded.body == body
    assert loaded.capture.pinned is True
    assert load_captures(wiki_root=capture_wiki) == [loaded]


def test_native_registration_never_acquires_provider_or_embedder(capture_wiki: Path, mocker: MockerFixture) -> None:
    from mdwiki.capture import CaptureInput
    from mdwiki.capture_commands import add_capture

    provider = mocker.patch("mdwiki.llm.build_provider_from_config", side_effect=AssertionError("provider forbidden"))
    embedder = mocker.patch("mdwiki.ingest.get_default_embedder", side_effect=AssertionError("embedder forbidden"))
    result = add_capture(wiki_root=capture_wiki, capture=CaptureInput(kind="fact", title="T", body="B"), no_ingest=True)
    assert result.record.status == "pending"
    assert result.ingest_result is None
    provider.assert_not_called()
    embedder.assert_not_called()


def test_registration_holds_real_lock_during_publication(capture_wiki: Path, mocker: MockerFixture) -> None:
    from mdwiki.capture import CaptureInput, register_capture
    from mdwiki.init import _write_registration_sidecar

    def publish(path: Path, sidecar: dict[str, dict[str, object]]) -> None:
        with sqlite3.connect(capture_wiki / ".mdwiki/write.lock", timeout=0) as contender:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                contender.execute("BEGIN IMMEDIATE")
        _write_registration_sidecar(path, sidecar)

    mocker.patch("mdwiki.capture._write_registration_sidecar", side_effect=publish)
    record = register_capture(
        wiki_root=capture_wiki, capture=CaptureInput(kind="fact", title="Lock", body="Serialized capture publication.")
    )
    assert (capture_wiki / record.raw_path).is_file()


def test_provider_ingest_failure_leaves_capture_pending(capture_wiki: Path, mocker: MockerFixture) -> None:
    from mdwiki.capture import CaptureIngestError, CaptureInput, get_capture
    from mdwiki.capture_commands import add_capture

    mocker.patch("mdwiki.ingest.ingest_source", side_effect=RuntimeError("provider offline"))
    with pytest.raises(CaptureIngestError, match="mdwiki ingest --pending") as error:
        add_capture(wiki_root=capture_wiki, capture=CaptureInput(kind="fact", title="Retry", body="Retry this conclusion."))
    assert get_capture(wiki_root=capture_wiki, source_id=error.value.source_id).status == "pending"


def test_missing_raw_source_remains_inspectable_but_cannot_export(capture_wiki: Path) -> None:
    from mdwiki.capture import CaptureError, CaptureInput, export_capture, get_capture, register_capture

    record = register_capture(wiki_root=capture_wiki, capture=CaptureInput(kind="fact", title="Lost", body="Lost raw source."))
    (capture_wiki / record.raw_path).unlink()
    assert get_capture(wiki_root=capture_wiki, source_id=record.source_id).raw_text == ""
    with pytest.raises(CaptureError, match="missing"):
        export_capture(wiki_root=capture_wiki, source_id=record.source_id, path=Path("export.md"))


def test_retirement_cache_overlays_sidecar(capture_wiki: Path) -> None:
    from mdwiki.capture import CaptureInput, get_capture, register_capture

    record = register_capture(
        wiki_root=capture_wiki, capture=CaptureInput(kind="fact", title="Cached", body="Current retirement is cached.")
    )
    with connect(capture_wiki / ".mdwiki/state.db") as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS capture_state (source_id TEXT PRIMARY KEY, retired_json TEXT)")
        conn.execute("INSERT INTO capture_state VALUES (?, ?)", (record.source_id, json.dumps({"date": "2026-11-02", "reason": "Retired"})))
        conn.commit()
    assert get_capture(wiki_root=capture_wiki, source_id=record.source_id).capture.retired == {"date": "2026-11-02", "reason": "Retired"}


def test_generated_page_target_is_rejected(capture_wiki: Path) -> None:
    from mdwiki.capture import CaptureError, CaptureInput, register_capture

    page = capture_wiki / "wiki/concepts/generated.md"
    page.parent.mkdir()
    page.write_text("---\ngenerated: true\n---\n# Generated\n")
    with pytest.raises(CaptureError, match="generated"):
        register_capture(
            wiki_root=capture_wiki, capture=CaptureInput(kind="correction", title="T", body="B", supersedes=("wiki/concepts/generated.md",))
        )


def test_crlf_body_roundtrips_and_export_stays_current(capture_wiki: Path) -> None:
    from mdwiki.capture import CaptureInput, capture_export_state, export_capture, get_capture, register_capture

    body = "A verbatim conclusion has Windows lines.\r\nNext line remains verbatim.\r\n"
    record = register_capture(wiki_root=capture_wiki, capture=CaptureInput(kind="fact", title="Windows", body=body))
    loaded = get_capture(wiki_root=capture_wiki, source_id=record.source_id)
    assert loaded.body == body
    assert loaded.raw_text == record.raw_text
    exported = export_capture(wiki_root=capture_wiki, source_id=record.source_id, path=Path("windows.md"))
    assert exported.read_bytes() == f"<!-- mdwiki-capture: {record.source_id} -->\n{record.raw_text}".encode()
    current = get_capture(wiki_root=capture_wiki, source_id=record.source_id)
    assert capture_export_state(wiki_root=capture_wiki, record=current) == "current"
    export_capture(wiki_root=capture_wiki, source_id=record.source_id, path=exported)


def test_capture_kind_lookup_avoids_sidecar_for_file_sources(capture_wiki: Path, mocker: MockerFixture) -> None:
    from mdwiki.capture import capture_kind_for_source

    (capture_wiki / "file.md").write_text("# File\n\nA source file remains a file.\n")
    refresh_wiki(capture_wiki)
    with connect(capture_wiki / ".mdwiki/state.db") as conn:
        source_id = conn.execute("SELECT id FROM sources WHERE original_path='file.md'").fetchone()[0]
    mocker.patch("mdwiki.capture.load_captures", side_effect=AssertionError("sidecar must not be read"))
    assert capture_kind_for_source(wiki_root=capture_wiki, source_id=source_id) is None
    assert capture_kind_for_source(wiki_root=capture_wiki, source_id="missing") is None


def test_capture_kind_lookup_returns_typed_kind(capture_wiki: Path) -> None:
    from mdwiki.capture import CaptureInput, capture_kind_for_source, register_capture

    record = register_capture(
        wiki_root=capture_wiki, capture=CaptureInput(kind="correction", title="Correction", body="Corrected conclusion.")
    )
    assert capture_kind_for_source(wiki_root=capture_wiki, source_id=record.source_id) == "correction"

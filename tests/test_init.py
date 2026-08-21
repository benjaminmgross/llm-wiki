"""Tests for ``mdwiki.init`` — scaffold a wiki and register markdown sources."""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

import pytest

from mdwiki.init import NestedWikiError, _register_sources, init_wiki
from mdwiki.loaders import build_registry
from mdwiki.state import connect


@pytest.fixture
def fresh_target(tmp_path: Path) -> Path:
    """A clean directory containing three .md files at varying depths."""
    (tmp_path / "top.md").write_text("# Top\n\nfoo bar baz")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "a.md").write_text("# Sub A\n\nhello world")
    (sub / "b.md").write_text("# Sub B\n\nlorem ipsum")
    return tmp_path


@pytest.mark.unit
def test_fresh_init_creates_wiki_layout(fresh_target: Path) -> None:
    init_wiki(fresh_target)
    wiki = fresh_target / ".mdwiki"
    assert (wiki / "config.toml").is_file()
    assert (wiki / "state.db").is_file()
    assert (wiki / "schema.md").is_file()
    assert (wiki / ".gitignore").is_file()
    assert (fresh_target / "raw").is_dir()
    assert (fresh_target / "wiki").is_dir()


@pytest.mark.unit
def test_fresh_init_registers_all_md_files(fresh_target: Path) -> None:
    result = init_wiki(fresh_target)
    assert result.files_registered == 3
    with connect(fresh_target / ".mdwiki" / "state.db") as conn:
        rows = conn.execute("SELECT original_path, status FROM sources").fetchall()
    assert len(rows) == 3
    assert all(row["status"] == "pending" for row in rows)


@pytest.mark.unit
def test_fresh_init_copies_to_raw_with_content_addressed_name(fresh_target: Path) -> None:
    init_wiki(fresh_target)
    raw_files = list((fresh_target / "raw").glob("*.md"))
    assert len(raw_files) == 3
    expected_hash = hashlib.sha256(b"# Top\n\nfoo bar baz").hexdigest()[:12]
    matches = [p for p in raw_files if p.name.startswith(expected_hash)]
    assert len(matches) == 1, f"raw/ missing content-addressed file for top.md: {raw_files}"
    assert matches[0].read_text() == "# Top\n\nfoo bar baz"


@pytest.mark.unit
def test_fresh_init_records_correct_content_hash(fresh_target: Path) -> None:
    init_wiki(fresh_target)
    expected_hash = hashlib.sha256(b"# Top\n\nfoo bar baz").hexdigest()
    with connect(fresh_target / ".mdwiki" / "state.db") as conn:
        row = conn.execute("SELECT content_hash FROM sources WHERE original_path = 'top.md'").fetchone()
    assert row["content_hash"] == expected_hash


@pytest.mark.unit
def test_fresh_init_respects_gitignore(fresh_target: Path) -> None:
    (fresh_target / ".gitignore").write_text("ignored/\n")
    ignored = fresh_target / "ignored"
    ignored.mkdir()
    (ignored / "secret.md").write_text("# secret")
    result = init_wiki(fresh_target)
    assert result.files_registered == 3, "ignored/secret.md should not have been registered"


@pytest.mark.unit
def test_fresh_init_does_not_register_mdwiki_internals(fresh_target: Path) -> None:
    """Fresh init should not register .mdwiki/schema.md (or anything else under .mdwiki/) as a source."""
    init_wiki(fresh_target)
    with connect(fresh_target / ".mdwiki" / "state.db") as conn:
        rows = conn.execute("SELECT original_path FROM sources").fetchall()
    paths = [row["original_path"] for row in rows]
    assert all(not p.startswith(".mdwiki/") for p in paths), f"mdwiki internals leaked: {paths}"
    assert len(paths) == 3


@pytest.mark.unit
def test_init_refuses_nested_wiki(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / ".mdwiki").mkdir()
    child = parent / "child"
    child.mkdir()
    (child / "x.md").write_text("# x")
    with pytest.raises(NestedWikiError) as excinfo:
        init_wiki(child)
    assert "parent" in str(excinfo.value).lower()


@pytest.mark.unit
def test_init_is_idempotent_when_already_initialized(fresh_target: Path) -> None:
    first = init_wiki(fresh_target)
    assert first.created is True
    second = init_wiki(fresh_target)
    assert second.created is False
    assert second.files_registered == 0
    with connect(fresh_target / ".mdwiki" / "state.db") as conn:
        count = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    assert count == 3, "re-init should not duplicate sources"


@pytest.mark.unit
def test_init_writes_config_with_llm_section(fresh_target: Path) -> None:
    init_wiki(fresh_target)
    config_text = (fresh_target / ".mdwiki" / "config.toml").read_text()
    assert "[llm]" in config_text
    assert "provider" in config_text


@pytest.mark.unit
def test_init_writes_nontrivial_schema(fresh_target: Path) -> None:
    init_wiki(fresh_target)
    schema = (fresh_target / ".mdwiki" / "schema.md").read_text()
    assert "entity" in schema.lower()
    assert "concept" in schema.lower()
    assert "synthesis" in schema.lower()


@pytest.mark.unit
def test_init_gitignore_protects_state_db_and_undo(fresh_target: Path) -> None:
    init_wiki(fresh_target)
    gi = (fresh_target / ".mdwiki" / ".gitignore").read_text()
    assert "state.db" in gi
    assert "undo/" in gi
    # Round-2 S3: WAL mode produces -wal and -shm sidecars next to state.db.
    # They're per-process journal files and must not be committed.
    assert "state.db-wal" in gi
    assert "state.db-shm" in gi
    assert "write.lock" in gi


@pytest.mark.unit
def test_init_skips_filetypes_no_loader_claims(fresh_target: Path) -> None:
    """Files with no registered loader (e.g. raw images, unknown extensions) are silently skipped.

    Phase 2 expanded the registry to claim .txt / .py / .csv as well as .md, so the
    skip set is now narrower than v1.0 — verify with truly unsupported extensions.
    """
    (fresh_target / "image.png").write_bytes(b"\x89PNG")
    (fresh_target / "weird.zzz").write_text("nothing claims this")
    result = init_wiki(fresh_target)
    assert result.files_registered == 3


@pytest.mark.unit
def test_init_skips_empty_loader_output(tmp_path: Path) -> None:
    """A loader that returns "" (e.g. PdfLoader on a scanned PDF) is counted as empty_load_skipped.

    No raw/ file is written, no sources row is inserted, and InitResult tracks the count
    so the CLI can surface it in the post-init summary.
    """
    import io

    from pypdf import PdfWriter

    # Real markdown source — should register normally
    (tmp_path / "good.md").write_text("# Good\n\nbody")

    # Scanned-style PDF (1 blank page → no extractable text)
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    (tmp_path / "scanned.pdf").write_bytes(buf.getvalue())

    result = init_wiki(tmp_path)

    assert result.files_registered == 1, "only the .md should register; scanned PDF skipped"
    assert result.empty_load_skipped == 1
    # No raw/ file for the empty-extracted PDF
    raw_files = list((tmp_path / "raw").glob("*-scanned.md"))
    assert raw_files == [], f"empty-extracted PDF should not write raw/: {raw_files}"
    # No sources row for the empty-extracted PDF
    with connect(tmp_path / ".mdwiki" / "state.db") as conn:
        rows = conn.execute("SELECT original_path FROM sources").fetchall()
    assert {r["original_path"] for r in rows} == {"good.md"}


@pytest.mark.unit
def test_init_writes_sources_sidecar(fresh_target: Path) -> None:
    """init writes raw/.sources.json so rebuild can recover original_path even with no state.db."""
    import json

    init_wiki(fresh_target)
    sidecar_path = fresh_target / "raw" / ".sources.json"
    assert sidecar_path.is_file()
    sidecar = json.loads(sidecar_path.read_text())
    assert isinstance(sidecar, dict)
    assert len(sidecar) == 3
    for short_hash, meta in sidecar.items():
        assert len(short_hash) == 12
        assert "original_path" in meta
        assert "mtime" in meta
        assert isinstance(meta["mtime"], (int, float))


@pytest.mark.unit
def test_init_raw_path_in_db_is_relative(fresh_target: Path) -> None:
    init_wiki(fresh_target)
    with connect(fresh_target / ".mdwiki" / "state.db") as conn:
        row = conn.execute("SELECT raw_path FROM sources WHERE original_path = 'top.md'").fetchone()
    assert row["raw_path"].startswith("raw/")
    assert (fresh_target / row["raw_path"]).is_file()


@pytest.mark.unit
def test_init_skips_duplicate_content_without_crashing(tmp_path: Path) -> None:
    """Two files with identical content should result in one registered source, one dedup_skipped."""
    (tmp_path / "a.md").write_text("# Same\n\nidentical body")
    (tmp_path / "b.md").write_text("# Same\n\nidentical body")
    (tmp_path / "c.md").write_text("# Different\n\nunique body")

    result = init_wiki(tmp_path)

    assert result.files_registered == 2
    assert result.dedup_skipped == 1
    with connect(tmp_path / ".mdwiki" / "state.db") as conn:
        count = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    assert count == 2


@pytest.mark.unit
def test_init_no_orphan_raw_files_for_same_content_different_stems(tmp_path: Path) -> None:
    """Round-2 S2: two files with identical content but different stems used to
    leave the second file on disk with no DB row.

    Pre-check by primary key now skips the copyfile entirely on a duplicate.
    Exactly one ``raw/<hash>-<slug>.md`` should exist after init.
    """
    (tmp_path / "alpha.md").write_text("# Same\n\nbody")
    (tmp_path / "beta.md").write_text("# Same\n\nbody")

    result = init_wiki(tmp_path)

    raw_files = [p for p in (tmp_path / "raw").glob("*.md") if not p.name.startswith(".")]
    assert len(raw_files) == 1, f"expected exactly one raw file, got {[p.name for p in raw_files]}"
    assert result.files_registered == 1
    assert result.dedup_skipped == 1


@pytest.mark.unit
def test_init_skips_pre_existing_wiki_and_raw_dirs(tmp_path: Path) -> None:
    """Pre-populated wiki/ or raw/ folders should not pollute the source table."""
    (tmp_path / "real-source.md").write_text("# Real")
    (tmp_path / "wiki").mkdir()
    (tmp_path / "wiki" / "stale.md").write_text("# Stale page")
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "old.md").write_text("# Old raw")

    result = init_wiki(tmp_path)

    assert result.files_registered == 1
    with connect(tmp_path / ".mdwiki" / "state.db") as conn:
        rows = conn.execute("SELECT original_path FROM sources").fetchall()
    paths = [row["original_path"] for row in rows]
    assert paths == ["real-source.md"]


@pytest.mark.unit
def test_register_sources_merges_existing_sidecar(tmp_path: Path) -> None:
    """A second _register_sources call must not clobber the first run's sidecar.

    The sidecar at raw/.sources.json is the source of truth for ``mdwiki rebuild``.
    Refresh re-invokes ``_register_sources`` against an existing wiki; without
    a merge step, the second write overwrites the first batch's entries.
    """
    # Arrange — first init registers alpha.md
    (tmp_path / "alpha.md").write_text("# Alpha")
    init_wiki(tmp_path)
    sidecar_path = tmp_path / "raw" / ".sources.json"
    first_sidecar = json.loads(sidecar_path.read_text())
    assert len(first_sidecar) == 1

    # Act — drop a new file, then re-run _register_sources directly (mimics refresh)
    (tmp_path / "beta.md").write_text("# Beta")
    config = tomllib.loads((tmp_path / ".mdwiki" / "config.toml").read_text())
    registry = build_registry(config=config, provider=None)
    _register_sources(
        target=tmp_path,
        raw_dir=tmp_path / "raw",
        db_path=tmp_path / ".mdwiki" / "state.db",
        registry=registry,
    )

    # Assert — sidecar contains BOTH alpha and beta entries
    merged_sidecar = json.loads(sidecar_path.read_text())
    assert len(merged_sidecar) == 2
    assert set(merged_sidecar.keys()) >= set(first_sidecar.keys())

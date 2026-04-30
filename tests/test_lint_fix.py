"""Tests for ``mdwiki.lint_fix`` — interactive remediation of lint findings.

Default mode handles deterministic fixes only (broken-ref → strip the link).
``mode="full"`` opts in to LLM-touching fixes (stale → re-ingest, coverage-gap →
re-ingest). Each fix runs through ``IngestTransaction`` so it's undoable via
``mdwiki undo``.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from mdwiki.init import init_wiki
from mdwiki.lint import lint_wiki
from mdwiki.lint_fix import LintFixResult, lint_fix
from mdwiki.state import connect


def _add_page(wiki_root: Path, *, path: str, content: str, kind: str = "concept") -> None:
    (wiki_root / path).parent.mkdir(parents=True, exist_ok=True)
    (wiki_root / path).write_text(content)
    db_path = wiki_root / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO pages (path, kind, last_touched_at) VALUES (?, ?, ?)",
            (path, kind, time.time()),
        )
        conn.commit()


@pytest.fixture
def wiki_with_broken_ref(tmp_path: Path) -> Path:
    """A wiki with one page containing a link to a non-existent target."""
    init_wiki(tmp_path)
    _add_page(
        tmp_path,
        path="wiki/concepts/a.md",
        content="# A\n\nThis links to [Missing Topic](missing.md) which doesn't exist.\n",
    )
    return tmp_path


@pytest.mark.unit
def test_lint_fix_strips_broken_links_in_default_mode(wiki_with_broken_ref: Path) -> None:
    """Default mode: broken-ref findings → link replaced with bare text; lint goes clean."""
    report_before = lint_wiki(wiki_with_broken_ref)
    assert any(f.kind == "broken-ref" for f in report_before.findings)

    result = lint_fix(wiki_with_broken_ref, mode="default", yes=True)

    assert isinstance(result, LintFixResult)
    assert result.fixed_count >= 1

    page = (wiki_with_broken_ref / "wiki" / "concepts" / "a.md").read_text()
    assert "[Missing Topic](missing.md)" not in page
    assert "Missing Topic" in page  # bare text preserved

    report_after = lint_wiki(wiki_with_broken_ref)
    assert not any(f.kind == "broken-ref" for f in report_after.findings)


@pytest.mark.unit
def test_lint_fix_user_can_skip_a_finding(wiki_with_broken_ref: Path) -> None:
    """When ``confirm`` returns False, the finding is left alone and counted as skipped."""
    result = lint_fix(
        wiki_with_broken_ref,
        mode="default",
        yes=False,
        confirm=lambda _f: False,
    )
    assert result.fixed_count == 0
    assert result.skipped_count >= 1
    page = (wiki_with_broken_ref / "wiki" / "concepts" / "a.md").read_text()
    assert "[Missing Topic](missing.md)" in page


@pytest.mark.unit
def test_lint_fix_default_mode_skips_orphan_findings(tmp_path: Path) -> None:
    """Default mode is deterministic-only — orphan findings require LLM or human judgment."""
    init_wiki(tmp_path)
    _add_page(tmp_path, path="wiki/concepts/island.md", content="# Island\n\nNobody links here.\n")

    result = lint_fix(tmp_path, mode="default", yes=True)
    # orphan should NOT have been fixed (no LLM round-trip in default mode)
    page = (tmp_path / "wiki" / "concepts" / "island.md").read_text()
    assert "Nobody links here." in page
    assert result.fixed_count == 0


@pytest.mark.unit
def test_lint_fix_default_mode_skips_stale_findings(tmp_path: Path, mocker: MockerFixture) -> None:
    """Default mode does not re-ingest stale sources (that costs LLM dollars)."""
    init_wiki(tmp_path)
    # Set up a stale state: page touched at t=1000, source mtime = t=2000
    db_path = tmp_path / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sources (id, original_path, raw_path, content_hash, mtime, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("src-aaa", "doc.md", "raw/aaa-doc.md", "abc" * 10, 2000.0, "ingested"),
        )
        conn.execute(
            "INSERT INTO pages (path, kind, last_touched_at) VALUES (?, ?, ?)",
            ("wiki/concepts/x.md", "concept", 1000.0),
        )
        conn.execute(
            "INSERT INTO backrefs (page_path, source_id, section_anchor, quote) "
            "VALUES (?, ?, ?, ?)",
            ("wiki/concepts/x.md", "src-aaa", "doc.md/Intro", "q"),
        )
        conn.commit()
    (tmp_path / "wiki" / "concepts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "wiki" / "concepts" / "x.md").write_text("# X\n\nContent.\n")

    # Mock ingest_source to verify it's NOT called in default mode
    mock_ingest = mocker.patch("mdwiki.lint_fix.ingest_source")
    result = lint_fix(tmp_path, mode="default", yes=True)
    mock_ingest.assert_not_called()
    assert result.skipped_count >= 1


@pytest.mark.unit
def test_lint_fix_full_mode_re_ingests_stale(tmp_path: Path, mocker: MockerFixture) -> None:
    """Full mode: stale findings trigger ingest_source(force=True)."""
    init_wiki(tmp_path)
    db_path = tmp_path / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sources (id, original_path, raw_path, content_hash, mtime, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("src-aaa", "doc.md", "raw/aaa-doc.md", "abc" * 10, 2000.0, "ingested"),
        )
        conn.execute(
            "INSERT INTO pages (path, kind, last_touched_at) VALUES (?, ?, ?)",
            ("wiki/concepts/x.md", "concept", 1000.0),
        )
        conn.execute(
            "INSERT INTO backrefs (page_path, source_id, section_anchor, quote) "
            "VALUES (?, ?, ?, ?)",
            ("wiki/concepts/x.md", "src-aaa", "doc.md/Intro", "q"),
        )
        conn.commit()
    (tmp_path / "wiki" / "concepts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "wiki" / "concepts" / "x.md").write_text("# X\n\nContent.\n")

    from mdwiki.ingest import IngestResult

    mock_ingest = mocker.patch("mdwiki.lint_fix.ingest_source")
    mock_ingest.return_value = IngestResult(source_id="src-aaa", applied=True, message="ok")

    result = lint_fix(tmp_path, mode="full", yes=True)
    mock_ingest.assert_called_once()
    kwargs = mock_ingest.call_args.kwargs
    assert kwargs.get("force") is True
    assert result.fixed_count >= 1


@pytest.mark.unit
def test_lint_fix_returns_zero_when_no_findings(tmp_path: Path) -> None:
    """Clean wiki → fixed_count == 0, skipped_count == 0."""
    init_wiki(tmp_path)
    result = lint_fix(tmp_path, mode="default", yes=True)
    assert result.fixed_count == 0
    assert result.skipped_count == 0

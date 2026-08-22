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
@pytest.mark.parametrize(
    ("source_link", "definition", "bare_text"),
    [
        ("[Broken][ref]", "\n\n[ref]: missing.md\n", "Broken"),
        ('[Broken](<missing file.md> "Title")', "", "Broken"),
        (r"[Bro\*ken](missing.md)", "", r"Bro\*ken"),
    ],
)
def test_lint_fix_uses_parser_backed_source_forms(
    tmp_path: Path,
    source_link: str,
    definition: str,
    bare_text: str,
) -> None:
    init_wiki(tmp_path)
    _add_page(
        tmp_path,
        path="wiki/concepts/a.md",
        content=f"# A\n\nBefore {source_link} after.{definition}",
    )

    result = lint_fix(tmp_path, mode="default", yes=True)

    assert result.fixed_count == 1
    page = (tmp_path / "wiki/concepts/a.md").read_text()
    assert source_link not in page
    assert f"Before {bare_text} after." in page


@pytest.mark.unit
@pytest.mark.parametrize(
    "example",
    [
        "`[Broken](missing.md)`",
        "```md\n[Broken](missing.md)\n```",
    ],
)
def test_lint_fix_fails_closed_when_source_form_is_not_unique(tmp_path: Path, example: str) -> None:
    init_wiki(tmp_path)
    page = tmp_path / "wiki/concepts/a.md"
    original = f"# A\n\n[Broken](missing.md)\n\n{example}\n"
    _add_page(tmp_path, path="wiki/concepts/a.md", content=original)

    result = lint_fix(tmp_path, mode="default", yes=True)

    assert result.fixed_count == 0
    assert page.read_text() == original


@pytest.mark.unit
def test_lint_fix_preserves_crlf_outside_exact_replacement(tmp_path: Path) -> None:
    init_wiki(tmp_path)
    page = tmp_path / "wiki/concepts/a.md"
    _add_page(tmp_path, path="wiki/concepts/a.md", content="# placeholder\n")
    page.write_bytes(b"# A\r\n\r\n[Broken](missing.md)\r\n")

    result = lint_fix(tmp_path, mode="default", yes=True)

    assert result.fixed_count == 1
    assert b"# A\r\n\r\nBroken\r\n" in page.read_bytes()


@pytest.mark.unit
def test_lint_fix_preserves_existing_crlf_frontmatter(tmp_path: Path) -> None:
    init_wiki(tmp_path)
    page = tmp_path / "wiki/concepts/a.md"
    _add_page(tmp_path, path="wiki/concepts/a.md", content="# placeholder\n")
    page.write_bytes(b"---\r\ntitle: A\r\n---\r\n# A\r\n\r\n[Broken](missing.md)\r\n")

    result = lint_fix(tmp_path, mode="default", yes=True)

    assert result.fixed_count == 1
    written = page.read_bytes()
    assert written.count(b"---\r\n") == 2
    assert b"title: A\r\nprevious_hash: " in written
    assert b"# A\r\n\r\nBroken\r\n" in written


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
            "INSERT INTO sources (id, original_path, raw_path, content_hash, mtime, status) VALUES (?, ?, ?, ?, ?, ?)",
            ("src-aaa", "doc.md", "raw/aaa-doc.md", "abc" * 10, 2000.0, "ingested"),
        )
        conn.execute(
            "INSERT INTO pages (path, kind, last_touched_at) VALUES (?, ?, ?)",
            ("wiki/concepts/x.md", "concept", 1000.0),
        )
        conn.execute(
            "INSERT INTO backrefs (page_path, source_id, section_anchor, quote) VALUES (?, ?, ?, ?)",
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
            "INSERT INTO sources (id, original_path, raw_path, content_hash, mtime, status) VALUES (?, ?, ?, ?, ?, ?)",
            ("src-aaa", "doc.md", "raw/aaa-doc.md", "abc" * 10, 2000.0, "ingested"),
        )
        conn.execute(
            "INSERT INTO pages (path, kind, last_touched_at) VALUES (?, ?, ?)",
            ("wiki/concepts/x.md", "concept", 1000.0),
        )
        conn.execute(
            "INSERT INTO backrefs (page_path, source_id, section_anchor, quote) VALUES (?, ?, ?, ?)",
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
def test_lint_fix_full_mode_stops_after_consecutive_failures(tmp_path: Path, mocker: MockerFixture) -> None:
    """Full mode should not spend through an entire backlog after repeated LLM failures."""
    init_wiki(tmp_path)
    _add_page(tmp_path, path="wiki/concepts/orphan-after-break.md", content="# Orphan\n\nNobody links here.\n")
    db_path = tmp_path / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        for index in range(3):
            source_id = f"src-{index}"
            original_path = f"doc-{index}.md"
            conn.execute(
                "INSERT INTO sources (id, original_path, raw_path, content_hash, mtime, status) VALUES (?, ?, ?, ?, ?, ?)",
                (source_id, original_path, f"raw/{source_id}.md", f"{index}" * 64, 1000.0, "ingested"),
            )
        conn.commit()

    mock_ingest = mocker.patch("mdwiki.lint_fix.ingest_source", side_effect=RuntimeError("same LLM failure"))

    result = lint_fix(tmp_path, mode="full", yes=True, max_consecutive_failures=2)

    assert mock_ingest.call_count == 2
    assert result.fixed_count == 0
    assert result.failed_count == 2
    assert result.skipped_count == 2


@pytest.mark.unit
def test_lint_fix_returns_zero_when_no_findings(tmp_path: Path) -> None:
    """Clean wiki → fixed_count == 0, skipped_count == 0."""
    init_wiki(tmp_path)
    result = lint_fix(tmp_path, mode="default", yes=True)
    assert result.fixed_count == 0
    assert result.skipped_count == 0

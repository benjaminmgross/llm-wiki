"""Tests for ``mdwiki.lint`` — wiki health checks."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from mdwiki.init import init_wiki
from mdwiki.lint import LintFinding, lint_wiki
from mdwiki.state import connect


@pytest.fixture
def wiki(tmp_path: Path) -> Path:
    init_wiki(tmp_path)
    return tmp_path


def _add_page(wiki_root: Path, *, path: str, content: str = "# X\n\nbody", kind: str = "concept") -> None:
    (wiki_root / path).parent.mkdir(parents=True, exist_ok=True)
    (wiki_root / path).write_text(content)
    db_path = wiki_root / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO pages (path, kind, last_touched_at) VALUES (?, ?, ?)",
            (path, kind, time.time()),
        )
        conn.commit()


@pytest.mark.unit
def test_lint_clean_wiki_returns_no_findings(wiki: Path) -> None:
    report = lint_wiki(wiki)
    assert report.findings == ()
    assert report.findings_by_kind == {}


@pytest.mark.unit
def test_lint_detects_broken_cross_ref(wiki: Path) -> None:
    _add_page(wiki, path="wiki/concepts/a.md", content="# A\n\nLinks to [B](b.md) which doesn't exist.")
    report = lint_wiki(wiki)
    broken = [f for f in report.findings if f.kind == "broken-ref"]
    assert len(broken) == 1
    assert "b.md" in broken[0].message


@pytest.mark.unit
@pytest.mark.parametrize(
    "target_name",
    ["target with spaces.md", "target (v2).md", "target#section.md", "target%done.md", "café.md"],
)
def test_lint_resolves_percent_encoded_semantic_page_targets(wiki: Path, target_name: str) -> None:
    from urllib.parse import quote

    encoded = quote(target_name, safe="/-._~")
    _add_page(wiki, path="wiki/concepts/source.md", content=f"# Source\n\n[Target]({encoded})")
    _add_page(wiki, path=f"wiki/concepts/{target_name}")

    report = lint_wiki(wiki)

    assert not any(f.kind == "broken-ref" for f in report.findings)
    assert not any(f.kind == "orphan" and f.page_path.endswith(target_name) for f in report.findings)


@pytest.mark.unit
@pytest.mark.parametrize(
    "destination",
    ['target.md "Helpful title"', "target.md 'Helpful title'", "target.md (Helpful title)", '<target.md> "Helpful title"'],
)
def test_lint_recognizes_inline_links_with_optional_titles(wiki: Path, destination: str) -> None:
    _add_page(wiki, path="wiki/concepts/source.md", content=f"# Source\n\n[Target]({destination})")
    _add_page(wiki, path="wiki/concepts/target.md")

    report = lint_wiki(wiki)

    assert not any(f.kind == "broken-ref" for f in report.findings)
    assert not any(f.kind == "orphan" and f.page_path.endswith("target.md") for f in report.findings)


@pytest.mark.unit
def test_lint_skips_external_links(wiki: Path) -> None:
    _add_page(
        wiki,
        path="wiki/concepts/a.md",
        content="# A\n\nLinks to [example](https://example.com) and [github](http://github.com).",
    )
    report = lint_wiki(wiki)
    assert not any(f.kind == "broken-ref" for f in report.findings)


@pytest.mark.unit
def test_lint_detects_orphan_pages(wiki: Path) -> None:
    """A page that's not linked from anywhere is an orphan."""
    _add_page(wiki, path="wiki/concepts/a.md", content="# A\n\nLinks to [B](b.md).")
    _add_page(wiki, path="wiki/concepts/b.md", content="# B\n\nLinks to [A](a.md).")
    _add_page(wiki, path="wiki/concepts/orphan.md", content="# Orphan\n\nlinks nowhere relevant")

    report = lint_wiki(wiki)
    orphans = [f.page_path for f in report.findings if f.kind == "orphan"]
    assert "wiki/concepts/orphan.md" in orphans
    assert "wiki/concepts/a.md" not in orphans  # linked from b
    assert "wiki/concepts/b.md" not in orphans  # linked from a


@pytest.mark.unit
def test_lint_self_link_does_not_hide_orphan(wiki: Path) -> None:
    """Only links from another semantic page satisfy the inbound-edge rule."""
    page_path = "wiki/concepts/self-linked.md"
    _add_page(wiki, path=page_path, content="# Self-linked\n\n[Self](self-linked.md)")

    report = lint_wiki(wiki)

    assert any(f.kind == "orphan" and f.page_path == page_path for f in report.findings)


@pytest.mark.unit
def test_lint_index_and_log_never_flagged_as_orphans(wiki: Path) -> None:
    """index.md and log.md are infrastructure, not pages."""
    (wiki / "wiki" / "index.md").write_text("# Index")
    (wiki / "wiki" / "log.md").write_text("- entry")
    report = lint_wiki(wiki)
    assert not any(f.page_path in ("wiki/index.md", "wiki/log.md") for f in report.findings)


@pytest.mark.unit
def test_lint_detects_stale_pages(wiki: Path) -> None:
    """Page is stale if any source it cites was modified after page.last_touched_at."""
    db_path = wiki / ".mdwiki" / "state.db"
    (wiki / "raw").mkdir(exist_ok=True)
    raw_file = wiki / "raw" / "abc-source.md"
    raw_file.write_text("# source")

    _add_page(wiki, path="wiki/concepts/from-source.md")
    with connect(db_path) as conn:
        conn.execute("UPDATE pages SET last_touched_at = ? WHERE path = ?", (1.0, "wiki/concepts/from-source.md"))
        conn.execute(
            "INSERT INTO sources (id, original_path, raw_path, content_hash, mtime, status) VALUES (?, ?, ?, ?, ?, ?)",
            ("abc123source", "src.md", "raw/abc-source.md", "h" * 64, 9999999999.0, "ingested"),
        )
        conn.execute(
            "INSERT INTO backrefs (page_path, source_id, quote) VALUES (?, ?, ?)",
            ("wiki/concepts/from-source.md", "abc123source", "some quote here"),
        )
        conn.commit()

    report = lint_wiki(wiki)
    stale = [f for f in report.findings if f.kind == "stale"]
    assert len(stale) == 1
    assert stale[0].page_path == "wiki/concepts/from-source.md"


@pytest.mark.unit
def test_lint_detects_coverage_gap_zero_backrefs(wiki: Path) -> None:
    """A source marked ingested but with zero backrefs was poorly absorbed."""
    db_path = wiki / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO sources (id, original_path, raw_path, content_hash, mtime, status, ingested_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("ghosthash123", "ghost.md", "raw/ghost.md", "h" * 64, 1.0, "ingested", 2.0),
        )
        conn.commit()

    report = lint_wiki(wiki)
    gaps = [f for f in report.findings if f.kind == "coverage-gap"]
    assert len(gaps) == 1
    assert "ghost.md" in gaps[0].message


@pytest.mark.unit
def test_lint_records_event_when_run(wiki: Path) -> None:
    db_path = wiki / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        before = conn.execute("SELECT COUNT(*) AS c FROM events WHERE kind='lint'").fetchone()["c"]
    assert before == 0

    lint_wiki(wiki)

    with connect(db_path) as conn:
        after = conn.execute("SELECT COUNT(*) AS c FROM events WHERE kind='lint'").fetchone()["c"]
    assert after == 1


@pytest.mark.unit
def test_lint_finding_is_immutable() -> None:
    f = LintFinding(kind="orphan", page_path="x.md", message="m", severity="warn")
    with pytest.raises(Exception):
        f.kind = "broken-ref"  # type: ignore[misc]


@pytest.mark.unit
def test_lint_findings_grouped_by_kind(wiki: Path) -> None:
    _add_page(wiki, path="wiki/concepts/orphan-1.md", content="# O1\n\nbody")
    _add_page(wiki, path="wiki/concepts/orphan-2.md", content="# O2\n\nbody")
    _add_page(wiki, path="wiki/concepts/has-broken.md", content="# X\n\n[Z](z.md)")

    report = lint_wiki(wiki)
    assert report.findings_by_kind.get("orphan", 0) >= 2
    assert report.findings_by_kind.get("broken-ref", 0) >= 1

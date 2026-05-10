"""Tests for ``mdwiki.status`` — read-only summary of wiki state."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from mdwiki.init import init_wiki
from mdwiki.state import connect
from mdwiki.status import StatusReport, format_status, get_status


@pytest.fixture
def populated_wiki(tmp_path: Path) -> Path:
    """A wiki with 3 pending sources and (optionally) a couple of events."""
    (tmp_path / "a.md").write_text("# A\nbody")
    (tmp_path / "b.md").write_text("# B\nbody")
    (tmp_path / "c.md").write_text("# C\nbody")
    init_wiki(tmp_path)
    return tmp_path


@pytest.mark.unit
def test_get_status_counts_pending_and_ingested(populated_wiki: Path) -> None:
    report = get_status(populated_wiki)
    assert report.pending == 3
    assert report.ingested == 0


@pytest.mark.unit
def test_get_status_counts_pages_by_kind(populated_wiki: Path) -> None:
    db_path = populated_wiki / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO pages (path, kind, last_touched_at) VALUES (?, ?, ?)",
            ("wiki/concepts/foo.md", "concept", time.time()),
        )
        conn.execute(
            "INSERT INTO pages (path, kind, last_touched_at) VALUES (?, ?, ?)",
            ("wiki/concepts/bar.md", "concept", time.time()),
        )
        conn.execute(
            "INSERT INTO pages (path, kind, last_touched_at) VALUES (?, ?, ?)",
            ("wiki/entities/baz.md", "entity", time.time()),
        )
    report = get_status(populated_wiki)
    assert report.pages_total == 3
    assert report.pages_by_kind["concept"] == 2
    assert report.pages_by_kind["entity"] == 1


@pytest.mark.unit
def test_get_status_recent_events_in_reverse_chronological_order(populated_wiki: Path) -> None:
    db_path = populated_wiki / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        conn.execute("INSERT INTO events (ts, kind, summary) VALUES (?, ?, ?)", (1.0, "ingest", "first"))
        conn.execute("INSERT INTO events (ts, kind, summary) VALUES (?, ?, ?)", (2.0, "ingest", "second"))
        conn.execute("INSERT INTO events (ts, kind, summary) VALUES (?, ?, ?)", (3.0, "lint", "third"))
    report = get_status(populated_wiki, recent_event_limit=2)
    assert len(report.recent_events) == 2
    assert report.recent_events[0].summary == "third"
    assert report.recent_events[1].summary == "second"


@pytest.mark.unit
def test_get_status_last_lint_is_most_recent_lint_event(populated_wiki: Path) -> None:
    db_path = populated_wiki / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        conn.execute("INSERT INTO events (ts, kind, summary) VALUES (?, ?, ?)", (5.0, "lint", "first lint"))
        conn.execute("INSERT INTO events (ts, kind, summary) VALUES (?, ?, ?)", (10.0, "lint", "second lint"))
        conn.execute("INSERT INTO events (ts, kind, summary) VALUES (?, ?, ?)", (15.0, "ingest", "after lints"))
    report = get_status(populated_wiki)
    assert report.last_lint_ts == 10.0


@pytest.mark.unit
def test_get_status_last_lint_is_none_when_no_lint_events(populated_wiki: Path) -> None:
    report = get_status(populated_wiki)
    assert report.last_lint_ts is None


@pytest.mark.unit
def test_format_status_renders_human_readable(populated_wiki: Path) -> None:
    report = get_status(populated_wiki)
    out = format_status(report, wiki_root=populated_wiki)
    assert "3 pending" in out
    assert "0 ingested" in out
    assert "never" in out.lower()
    assert str(populated_wiki) in out


@pytest.mark.unit
def test_status_warns_when_disk_pages_exist_but_state_has_no_pages(populated_wiki: Path) -> None:
    (populated_wiki / "wiki" / "concepts").mkdir(parents=True)
    (populated_wiki / "wiki" / "concepts" / "drift.md").write_text("# Drift\n\nExists only on disk.")

    report = get_status(populated_wiki)
    out = format_status(report, wiki_root=populated_wiki)

    assert report.disk_pages_total == 1
    assert report.pages_total == 0
    assert "WARNING: wiki files exist on disk, but state.db has no page rows." in out
    assert "Run `mdwiki rebuild --pages` before ingesting." in out


@pytest.mark.unit
def test_status_warns_on_material_page_count_mismatch(populated_wiki: Path) -> None:
    (populated_wiki / "wiki" / "concepts").mkdir(parents=True)
    (populated_wiki / "wiki" / "concepts" / "one.md").write_text("# One\n\nOn disk.")
    (populated_wiki / "wiki" / "concepts" / "two.md").write_text("# Two\n\nOn disk.")
    with connect(populated_wiki / ".mdwiki" / "state.db") as conn:
        conn.execute(
            "INSERT INTO pages (path, kind, last_touched_at) VALUES (?, ?, ?)",
            ("wiki/concepts/one.md", "concept", time.time()),
        )

    report = get_status(populated_wiki)
    out = format_status(report, wiki_root=populated_wiki)

    assert report.disk_pages_total == 2
    assert report.pages_total == 1
    assert "WARNING: wiki page count drift" in out


@pytest.mark.unit
def test_format_status_with_events_lists_them(populated_wiki: Path) -> None:
    db_path = populated_wiki / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        conn.execute("INSERT INTO events (ts, kind, summary) VALUES (?, ?, ?)", (1700000000.0, "ingest", "ingested foo.md"))
    report = get_status(populated_wiki)
    out = format_status(report, wiki_root=populated_wiki)
    assert "ingested foo.md" in out


@pytest.mark.unit
def test_status_report_is_immutable_dataclass() -> None:
    report = StatusReport(
        pending=1,
        ingested=2,
        pages_total=0,
        pages_by_kind={},
        events_total=0,
        last_lint_ts=None,
        recent_events=(),
    )
    with pytest.raises(Exception):
        report.pending = 99  # type: ignore[misc]

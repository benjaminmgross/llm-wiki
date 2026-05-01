"""Tests for ``mdwiki.rebuild_log`` — regenerate ``wiki/log.md`` from the events table.

Recovery utility: handles the rare KeyboardInterrupt-between-DB-commit-and-log-write
window noted as round-2 S6 in the v1.0 review. The events table is the source of
truth; ``log.md`` is a flat representation that can be reproduced from it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mdwiki.init import init_wiki
from mdwiki.rebuild_log import RebuildLogResult, rebuild_log
from mdwiki.state import connect


def _seed_events(db_path: Path, events: list[tuple[float, str, str, str | None]]) -> None:
    """Insert events (and the transactions rows they reference, satisfying the FK).

    Each tuple: ``(ts, kind, summary, transaction_id)``. When ``transaction_id``
    is non-null, also inserts a placeholder ``transactions`` row so the events
    FK is satisfied — tests model the post-commit state.
    """
    with connect(db_path) as conn:
        for ts, _kind, _summary, tx_id in events:
            if tx_id is not None:
                conn.execute(
                    "INSERT OR IGNORE INTO transactions (id, ts, undo_snapshot_path, applied) "
                    "VALUES (?, ?, ?, 1)",
                    (tx_id, ts, None),
                )
        for ts, kind, summary, tx_id in events:
            conn.execute(
                "INSERT INTO events (ts, kind, summary, transaction_id) VALUES (?, ?, ?, ?)",
                (ts, kind, summary, tx_id),
            )
        conn.commit()


@pytest.mark.unit
def test_rebuild_log_regenerates_from_events_table(tmp_path: Path) -> None:
    """Two ingest events → log.md has two lines in chronological order."""
    init_wiki(tmp_path)
    db_path = tmp_path / ".mdwiki" / "state.db"
    _seed_events(
        db_path,
        [
            (1000.0, "ingest", "ingest a.md", "tx-aaa"),
            (2000.0, "ingest", "ingest b.md", "tx-bbb"),
        ],
    )

    log_path = tmp_path / "wiki" / "log.md"
    log_path.unlink(missing_ok=True)

    result = rebuild_log(tmp_path)

    assert isinstance(result, RebuildLogResult)
    assert result.lines_written == 2
    log_text = log_path.read_text()
    assert log_text.count("\n") == 2
    assert "ingest a.md" in log_text
    assert "ingest b.md" in log_text
    assert log_text.index("ingest a.md") < log_text.index("ingest b.md")


@pytest.mark.unit
def test_rebuild_log_uses_transaction_id_format(tmp_path: Path) -> None:
    """Each line matches the format ``- <iso_ts> [<tx_id>] <summary>``."""
    init_wiki(tmp_path)
    db_path = tmp_path / ".mdwiki" / "state.db"
    _seed_events(db_path, [(1700000000.0, "ingest", "ingest x.md", "tx-test123")])

    rebuild_log(tmp_path)
    line = (tmp_path / "wiki" / "log.md").read_text().rstrip()
    # Format: - 2023-11-14T22:13:20Z [tx-test123] ingest x.md
    assert line.startswith("- ")
    assert "[tx-test123]" in line
    assert "ingest x.md" in line
    # ISO 8601 UTC timestamp ending in Z, followed by a space and the [tx_id] block
    assert "Z [" in line


@pytest.mark.unit
def test_rebuild_log_skips_events_without_transaction_id(tmp_path: Path) -> None:
    """Lint events (no tx_id) aren't in log.md by design — skip them on rebuild."""
    init_wiki(tmp_path)
    db_path = tmp_path / ".mdwiki" / "state.db"
    _seed_events(
        db_path,
        [
            (1000.0, "ingest", "ingest a.md", "tx-aaa"),
            (1500.0, "lint", "lint: 3 findings", None),  # no tx_id — skip
            (2000.0, "ingest", "ingest b.md", "tx-bbb"),
        ],
    )

    result = rebuild_log(tmp_path)
    log_text = (tmp_path / "wiki" / "log.md").read_text()
    assert result.lines_written == 2
    assert "lint: 3 findings" not in log_text


@pytest.mark.unit
def test_rebuild_log_idempotent(tmp_path: Path) -> None:
    """Running rebuild_log twice produces the same file (no doubling)."""
    init_wiki(tmp_path)
    db_path = tmp_path / ".mdwiki" / "state.db"
    _seed_events(db_path, [(1000.0, "ingest", "ingest a.md", "tx-aaa")])

    rebuild_log(tmp_path)
    first = (tmp_path / "wiki" / "log.md").read_text()
    rebuild_log(tmp_path)
    second = (tmp_path / "wiki" / "log.md").read_text()

    assert first == second


@pytest.mark.unit
def test_rebuild_log_creates_wiki_dir_if_missing(tmp_path: Path) -> None:
    """If wiki/ doesn't exist (corrupt repo), rebuild_log creates it."""
    init_wiki(tmp_path)
    # Simulate the wiki/ folder being deleted but state.db intact
    import shutil

    shutil.rmtree(tmp_path / "wiki")
    db_path = tmp_path / ".mdwiki" / "state.db"
    _seed_events(db_path, [(1000.0, "ingest", "ingest a.md", "tx-aaa")])

    result = rebuild_log(tmp_path)
    assert (tmp_path / "wiki" / "log.md").is_file()
    assert result.lines_written == 1


@pytest.mark.unit
def test_rebuild_log_zero_events_writes_empty_file(tmp_path: Path) -> None:
    """No events → empty log.md (still creates the file for downstream tools)."""
    init_wiki(tmp_path)
    log_path = tmp_path / "wiki" / "log.md"
    log_path.unlink(missing_ok=True)
    result = rebuild_log(tmp_path)
    assert log_path.is_file()
    assert log_path.read_text() == ""
    assert result.lines_written == 0

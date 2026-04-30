"""Tests for bulk ingest (``ingest_many``) — phase 6."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from mdwiki.ingest import IngestError, ingest_many
from mdwiki.init import init_wiki
from mdwiki.llm.base import CompleteResult
from mdwiki.state import connect


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")


def _plan_for(*, page_path: str, section_id: str) -> str:
    return json.dumps(
        {
            "verdict": "ingest",
            "rationale": "x",
            "updates": [],
            "new_pages": [
                {
                    "path": page_path,
                    "kind": "concept",
                    "content": "# X\n\nbody",
                    "claims": [{"source_section_id": section_id, "quote": "this paper introduces attention sinks for long contexts"}],
                }
            ],
            "cross_refs": [],
        }
    )


@pytest.fixture
def wiki_with_three_pending(tmp_path: Path) -> Path:
    for name in ("a", "b", "c"):
        (tmp_path / f"{name}.md").write_text(
            f"# {name}\n\n## Intro\n\nThis paper introduces attention sinks for long contexts.\n"
        )
    init_wiki(tmp_path)
    return tmp_path


@pytest.mark.unit
def test_ingest_many_pending_processes_all(wiki_with_three_pending: Path, mocker: MockerFixture) -> None:
    plans = iter(
        [
            _plan_for(page_path="wiki/concepts/p-a.md", section_id="a.md/Intro"),
            _plan_for(page_path="wiki/concepts/p-b.md", section_id="b.md/Intro"),
            _plan_for(page_path="wiki/concepts/p-c.md", section_id="c.md/Intro"),
        ]
    )
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.complete",
        side_effect=lambda **_kw: CompleteResult(text=next(plans), input_tokens=10, output_tokens=10),
    )
    results = ingest_many(wiki_with_three_pending, scope="pending", yes=True)
    assert len(results) == 3
    assert all(r.applied for r in results)


@pytest.mark.unit
def test_ingest_many_pending_skips_already_ingested(wiki_with_three_pending: Path, mocker: MockerFixture) -> None:
    """After one source is ingested, scope=pending should run only the other two."""
    db_path = wiki_with_three_pending / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        conn.execute("UPDATE sources SET status='ingested', ingested_at=1.0 WHERE original_path='a.md'")
        conn.commit()

    plans = iter(
        [
            _plan_for(page_path="wiki/concepts/p-b.md", section_id="b.md/Intro"),
            _plan_for(page_path="wiki/concepts/p-c.md", section_id="c.md/Intro"),
        ]
    )
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.complete",
        side_effect=lambda **_kw: CompleteResult(text=next(plans), input_tokens=10, output_tokens=10),
    )
    results = ingest_many(wiki_with_three_pending, scope="pending", yes=True)
    assert len(results) == 2


@pytest.mark.unit
def test_ingest_many_failure_does_not_abort_loop(wiki_with_three_pending: Path, mocker: MockerFixture) -> None:
    """One bad source should not prevent the remaining two from being processed."""
    counter = {"calls": 0}

    def fake_complete(**_kw):  # type: ignore[no-untyped-def]
        counter["calls"] += 1
        if counter["calls"] == 2:
            return CompleteResult(text="not valid json {{{", input_tokens=10, output_tokens=10)
        return CompleteResult(
            text=_plan_for(
                page_path=f"wiki/concepts/p-{counter['calls']}.md",
                section_id="a.md/Intro" if counter["calls"] == 1 else "c.md/Intro",
            ),
            input_tokens=10,
            output_tokens=10,
        )

    mocker.patch("mdwiki.llm.anthropic.AnthropicProvider.complete", side_effect=fake_complete)
    failures: list[tuple[str, Exception]] = []
    results = ingest_many(
        wiki_with_three_pending,
        scope="pending",
        yes=True,
        on_failure=lambda path, exc: failures.append((path, exc)),
    )
    assert len(results) == 3
    applied = [r for r in results if r.applied]
    failed = [r for r in results if not r.applied]
    assert len(applied) == 2
    assert len(failed) == 1
    assert len(failures) == 1
    assert isinstance(failures[0][1], IngestError)


@pytest.mark.unit
def test_ingest_many_progress_callback_receives_each_step(wiki_with_three_pending: Path, mocker: MockerFixture) -> None:
    plans = iter(
        [_plan_for(page_path=f"wiki/concepts/p{i}.md", section_id=f"{n}.md/Intro") for i, n in enumerate("abc")]
    )
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.complete",
        side_effect=lambda **_kw: CompleteResult(text=next(plans), input_tokens=10, output_tokens=10),
    )
    progress: list[tuple[int, int, str]] = []
    ingest_many(
        wiki_with_three_pending,
        scope="pending",
        yes=True,
        on_progress=lambda i, total, path: progress.append((i, total, path)),
    )
    assert [p[0] for p in progress] == [1, 2, 3]
    assert [p[1] for p in progress] == [3, 3, 3]


@pytest.mark.unit
def test_ingest_many_empty_returns_empty(tmp_path: Path) -> None:
    init_wiki(tmp_path)
    results = ingest_many(tmp_path, scope="pending", yes=True)
    assert results == []


@pytest.mark.unit
def test_ingest_many_all_includes_already_ingested(wiki_with_three_pending: Path, mocker: MockerFixture) -> None:
    """scope='all' should re-process even sources marked ingested."""
    db_path = wiki_with_three_pending / ".mdwiki" / "state.db"
    with connect(db_path) as conn:
        conn.execute("UPDATE sources SET status='ingested', ingested_at=1.0")
        conn.commit()

    plans = iter([_plan_for(page_path=f"wiki/concepts/{n}.md", section_id=f"{n}.md/Intro") for n in "abc"])
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.complete",
        side_effect=lambda **_kw: CompleteResult(text=next(plans), input_tokens=10, output_tokens=10),
    )
    results = ingest_many(wiki_with_three_pending, scope="all", yes=True)
    assert len(results) == 3

"""Tests for ``mdwiki.synthesize`` — synthesis channel B (topic + auto modes)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from pytest_mock import MockerFixture

from mdwiki.embeddings import serialize
from mdwiki.index import regenerate_index
from mdwiki.init import init_wiki
from mdwiki.llm.base import CompleteResult
from mdwiki.state import connect
from mdwiki.synthesize import (
    SynthesisError,
    build_cross_ref_graph,
    find_clusters,
    synthesize_auto,
    synthesize_topic,
)


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")


def _seed_pages(tmp_path: Path, pages: list[tuple[str, str, str]]) -> Path:
    """``pages`` is a list of (rel_path, kind, content). Init + seed + index."""
    init_wiki(tmp_path)
    db_path = tmp_path / ".mdwiki" / "state.db"
    rng = np.random.default_rng(seed=7)
    with connect(db_path) as conn:
        for path, kind, content in pages:
            (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / path).write_text(content)
            vec = rng.standard_normal(384).astype("float32").tolist()
            conn.execute(
                "INSERT INTO pages (path, kind, embedding, last_touched_at) VALUES (?, ?, ?, ?)",
                (path, kind, serialize(vec), 1.0),
            )
        conn.commit()
    regenerate_index(tmp_path)
    return tmp_path


# --- Cross-ref graph extraction --------------------------------------------------

@pytest.mark.unit
def test_build_cross_ref_graph_extracts_relative_links(tmp_path: Path) -> None:
    wiki = _seed_pages(
        tmp_path,
        [
            ("wiki/concepts/a.md", "concept", "# A\n\nLinks to [B](b.md) and [C](../concepts/c.md)."),
            ("wiki/concepts/b.md", "concept", "# B\n\nbody"),
            ("wiki/concepts/c.md", "concept", "# C\n\nbody"),
        ],
    )
    graph = build_cross_ref_graph(wiki)
    assert "wiki/concepts/b.md" in graph["wiki/concepts/a.md"]
    assert "wiki/concepts/c.md" in graph["wiki/concepts/a.md"]
    assert graph["wiki/concepts/b.md"] == set()


@pytest.mark.unit
def test_build_cross_ref_graph_skips_index_and_log(tmp_path: Path) -> None:
    wiki = _seed_pages(
        tmp_path,
        [("wiki/concepts/x.md", "concept", "# X\n\nbody")],
    )
    (wiki / "wiki" / "log.md").write_text("- a line linking [x](concepts/x.md)\n")
    graph = build_cross_ref_graph(wiki)
    assert "wiki/log.md" not in graph
    assert "wiki/index.md" not in graph


# --- Cluster detection -----------------------------------------------------------

@pytest.mark.unit
def test_find_clusters_returns_connected_components_above_min_size() -> None:
    graph = {
        "a": {"b"},
        "b": {"a", "c"},
        "c": {"b"},
        "d": set(),  # isolated
        "e": {"f"},  # 2-page component, below min_size
        "f": {"e"},
    }
    clusters = find_clusters(graph, min_size=3)
    assert len(clusters) == 1
    assert clusters[0] == {"a", "b", "c"}


@pytest.mark.unit
def test_find_clusters_treats_edges_as_undirected() -> None:
    graph = {
        "a": {"b"},
        "b": set(),  # b doesn't list a explicitly but the edge a→b should still connect them
        "c": {"a"},
    }
    clusters = find_clusters(graph, min_size=3)
    assert len(clusters) == 1
    assert clusters[0] == {"a", "b", "c"}


@pytest.mark.unit
def test_find_clusters_empty_graph_returns_empty() -> None:
    assert find_clusters({}, min_size=3) == []


# --- Topic mode ------------------------------------------------------------------

@pytest.mark.unit
def test_synthesize_topic_writes_synthesis_page(tmp_path: Path, mocker: MockerFixture) -> None:
    wiki = _seed_pages(
        tmp_path,
        [
            ("wiki/concepts/a.md", "concept", "# A\n\nbody"),
            ("wiki/concepts/b.md", "concept", "# B\n\nbody"),
        ],
    )
    body = "# Topic\n\n## Section\n\nA cross-cutting writeup citing [A](../concepts/a.md) and [B](../concepts/b.md)."
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.complete",
        return_value=CompleteResult(text=body, input_tokens=100, output_tokens=80),
    )
    result = synthesize_topic(wiki, "topic", yes=True)
    assert result.applied is True
    assert result.filed_path is not None
    assert (wiki / result.filed_path).is_file()
    assert "wiki/syntheses/" in result.filed_path


@pytest.mark.unit
def test_synthesize_topic_respects_insufficient_coverage_refusal(tmp_path: Path, mocker: MockerFixture) -> None:
    wiki = _seed_pages(tmp_path, [("wiki/concepts/x.md", "concept", "# X\n\nbody")])
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.complete",
        return_value=CompleteResult(
            text="INSUFFICIENT_COVERAGE: the wiki has only one page on this topic.",
            input_tokens=10, output_tokens=10,
        ),
    )
    result = synthesize_topic(wiki, "obscure topic the wiki barely covers", yes=True)
    assert result.applied is False
    assert "insufficient" in result.message.lower()
    assert not (wiki / "wiki" / "syntheses").exists() or not list((wiki / "wiki" / "syntheses").iterdir())


@pytest.mark.unit
def test_synthesize_topic_respects_duplicate_refusal(tmp_path: Path, mocker: MockerFixture) -> None:
    wiki = _seed_pages(
        tmp_path, [("wiki/concepts/the-topic.md", "concept", "# The Topic\n\nfull writeup")]
    )
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.complete",
        return_value=CompleteResult(
            text="DUPLICATE_OF: wiki/concepts/the-topic.md",
            input_tokens=10, output_tokens=10,
        ),
    )
    result = synthesize_topic(wiki, "the topic", yes=True)
    assert result.applied is False
    assert "duplicate" in result.message.lower()


@pytest.mark.unit
def test_synthesize_topic_rejection_leaves_no_changes(tmp_path: Path, mocker: MockerFixture) -> None:
    wiki = _seed_pages(
        tmp_path,
        [
            ("wiki/concepts/a.md", "concept", "# A\n\nbody"),
            ("wiki/concepts/b.md", "concept", "# B\n\nbody"),
        ],
    )
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.complete",
        return_value=CompleteResult(text="# Topic\n\nbody", input_tokens=10, output_tokens=10),
    )
    pre = len(list((wiki / "wiki").rglob("*.md")))
    result = synthesize_topic(wiki, "topic", yes=False, confirm=lambda _body: False)
    post = len(list((wiki / "wiki").rglob("*.md")))
    assert result.applied is False
    assert pre == post


# --- Auto mode -------------------------------------------------------------------

@pytest.mark.unit
def test_synthesize_auto_skips_clusters_below_min_size(tmp_path: Path, mocker: MockerFixture) -> None:
    """Auto mode should not even consult the LLM for sub-threshold clusters."""
    wiki = _seed_pages(
        tmp_path,
        [
            ("wiki/concepts/a.md", "concept", "# A\n\nLinks [B](b.md)"),
            ("wiki/concepts/b.md", "concept", "# B\n\nLinks [A](a.md)"),
        ],
    )
    mock = mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.complete",
        return_value=CompleteResult(text='{"propose": false, "title": null, "rationale": "n/a"}', input_tokens=10, output_tokens=10),
    )
    proposals = synthesize_auto(wiki, yes=True, confirm_each=lambda _p: False)
    assert mock.call_count == 0
    assert proposals == []


@pytest.mark.unit
def test_synthesize_auto_proposes_for_cluster_and_can_apply(tmp_path: Path, mocker: MockerFixture) -> None:
    wiki = _seed_pages(
        tmp_path,
        [
            ("wiki/concepts/a.md", "concept", "# A\n\nLinks [B](b.md) and [C](c.md)"),
            ("wiki/concepts/b.md", "concept", "# B\n\nLinks [A](a.md) and [C](c.md)"),
            ("wiki/concepts/c.md", "concept", "# C\n\nLinks [A](a.md) and [B](b.md)"),
        ],
    )
    cluster_response = json.dumps({"propose": True, "title": "abc-pattern", "rationale": "they share a pattern"})
    page_body = "# ABC Pattern\n\nA cross-cutting writeup of [A](../concepts/a.md), [B](../concepts/b.md), [C](../concepts/c.md)."

    responses = iter(
        [
            CompleteResult(text=cluster_response, input_tokens=100, output_tokens=20),
            CompleteResult(text=page_body, input_tokens=200, output_tokens=80),
        ]
    )
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.complete",
        side_effect=lambda **_kwargs: next(responses),
    )

    proposals = synthesize_auto(wiki, yes=True, confirm_each=lambda _p: True)
    assert len(proposals) == 1
    assert proposals[0].applied is True
    assert proposals[0].filed_path is not None
    assert "abc-pattern" in proposals[0].filed_path


@pytest.mark.unit
def test_synthesize_auto_respects_no_synthesis_verdict(tmp_path: Path, mocker: MockerFixture) -> None:
    wiki = _seed_pages(
        tmp_path,
        [
            ("wiki/concepts/a.md", "concept", "# A\n\nLinks [B](b.md) and [C](c.md)"),
            ("wiki/concepts/b.md", "concept", "# B\n\nLinks [A](a.md) and [C](c.md)"),
            ("wiki/concepts/c.md", "concept", "# C\n\nLinks [A](a.md) and [B](b.md)"),
        ],
    )
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.complete",
        return_value=CompleteResult(text='{"propose": false, "title": null, "rationale": "obvious enough"}', input_tokens=10, output_tokens=10),
    )
    proposals = synthesize_auto(wiki, yes=True, confirm_each=lambda _p: True)
    assert len(proposals) == 1
    assert proposals[0].applied is False
    assert "no-synthesis" in proposals[0].message.lower() or "skip" in proposals[0].message.lower()


@pytest.mark.unit
def test_synthesize_topic_outside_wiki_raises(tmp_path: Path) -> None:
    with pytest.raises(SynthesisError):
        synthesize_topic(tmp_path, "anything")

"""Pinned rule retrieval without provider or embedding inference."""

from __future__ import annotations

from pathlib import Path

import pytest

from mdwiki.embeddings import serialize
from mdwiki.init import init_wiki
from mdwiki.state import connect


@pytest.fixture
def pinned_wiki(tmp_path: Path) -> Path:
    init_wiki(tmp_path)
    with connect(tmp_path / ".mdwiki" / "state.db") as conn:
        if "pinned" not in {row[1] for row in conn.execute("PRAGMA table_info(pages)")}:
            conn.execute("ALTER TABLE pages ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
        for index in range(14):
            path = f"wiki/concepts/p{index:02}.md"
            (tmp_path / path).parent.mkdir(exist_ok=True)
            (tmp_path / path).write_text(f"# Page {index}\n\nEvidence {index}.\n")
            vector = [1.0, 0.0] if index < 8 else [0.3, 0.9]
            conn.execute(
                "INSERT INTO pages (path, kind, embedding, last_touched_at, pinned) VALUES (?, 'concept', ?, 1, ?)",
                (path, serialize(vector), int(index >= 8)),
            )
    return tmp_path


def test_pin_boost_appends_relevant_ninth_page_and_caps_extra_rules(pinned_wiki: Path) -> None:
    from mdwiki.capture_candidates import append_pinned_candidates

    selected = [f"wiki/concepts/p{i:02}.md" for i in range(8)]
    result = append_pinned_candidates(wiki_root=pinned_wiki, query_vectors=[[1.0, 0.0]], selected_paths=selected)

    assert result[:8] == selected
    assert result[8:] == [f"wiki/concepts/p{i:02}.md" for i in range(8, 12)]


def test_pin_boost_respects_config_floor_and_cap_and_deduplicates(pinned_wiki: Path) -> None:
    from mdwiki.capture_candidates import append_pinned_candidates

    config = pinned_wiki / ".mdwiki" / "config.toml"
    config.write_text(config.read_text().replace("pin_max_candidates = 4", "pin_max_candidates = 1"))
    selected = ["wiki/concepts/p08.md"]
    result = append_pinned_candidates(wiki_root=pinned_wiki, query_vectors=[[1.0, 0.0]], selected_paths=selected)
    assert result == ["wiki/concepts/p08.md", "wiki/concepts/p09.md"]
    config.write_text(config.read_text().replace("pin_min_similarity = 0.2", "pin_min_similarity = 0.9"))
    assert append_pinned_candidates(wiki_root=pinned_wiki, query_vectors=[[1.0, 0.0]], selected_paths=[]) == []


def test_pin_boost_uses_best_section_and_skips_missing_or_unembedded_pages(pinned_wiki: Path) -> None:
    from mdwiki.capture_candidates import append_pinned_candidates

    (pinned_wiki / "wiki/concepts/p08.md").unlink()
    with connect(pinned_wiki / ".mdwiki/state.db") as conn:
        conn.execute("UPDATE pages SET embedding = NULL WHERE path = 'wiki/concepts/p09.md'")
    result = append_pinned_candidates(wiki_root=pinned_wiki, query_vectors=[[-1.0, 0.0], [0.0, 1.0]], selected_paths=[])
    assert result == [f"wiki/concepts/p{i:02}.md" for i in range(10, 14)]
    assert append_pinned_candidates(wiki_root=pinned_wiki, query_vectors=[], selected_paths=["existing"]) == ["existing"]


def test_query_candidates_append_pinned_page_below_top_eight(pinned_wiki: Path) -> None:
    from mdwiki.query import _find_candidate_pages_for_query

    candidates = _find_candidate_pages_for_query(wiki_root=pinned_wiki, query_vec=[1.0, 0.0])
    assert len(candidates) == 12
    assert candidates[8]["path"] == "wiki/concepts/p08.md"


def test_rule_context_preserves_body_without_embedding(pinned_wiki: Path) -> None:
    from mdwiki.capture_candidates import active_rule_context

    assert active_rule_context(wiki_root=pinned_wiki) == ""
    rules = "---\ngenerated: true\n---\n# Standing rules\n\nNever make unsupported ownership claims.\n"
    (pinned_wiki / "wiki/standing-rules.md").write_text(rules)
    assert active_rule_context(wiki_root=pinned_wiki) == rules


@pytest.mark.parametrize(
    "setting", ["pin_min_similarity = nan", "pin_min_similarity = true", "pin_max_candidates = -1", "pin_max_candidates = true"]
)
def test_pin_boost_rejects_invalid_limits(pinned_wiki: Path, setting: str) -> None:
    from mdwiki.capture_candidates import append_pinned_candidates

    config = pinned_wiki / ".mdwiki/config.toml"
    key = setting.split(" = ", 1)[0]
    original = "pin_min_similarity = 0.2" if key == "pin_min_similarity" else "pin_max_candidates = 4"
    config.write_text(config.read_text().replace(original, setting))
    with pytest.raises(ValueError, match=key):
        append_pinned_candidates(wiki_root=pinned_wiki, query_vectors=[[1.0, 0.0]], selected_paths=[])


def test_zero_pin_cap_disables_boost(pinned_wiki: Path) -> None:
    from mdwiki.capture_candidates import append_pinned_candidates

    config = pinned_wiki / ".mdwiki/config.toml"
    config.write_text(config.read_text().replace("pin_max_candidates = 4", "pin_max_candidates = 0"))
    assert append_pinned_candidates(wiki_root=pinned_wiki, query_vectors=[[1.0, 0.0]], selected_paths=[]) == []


def test_ingest_candidates_also_include_pinned_rules(pinned_wiki: Path) -> None:
    from mdwiki.ingest import _find_candidate_pages

    candidates = _find_candidate_pages(wiki_root=pinned_wiki, section_vectors=[[1.0, 0.0]])
    assert "wiki/concepts/p08.md" in {page["path"] for page in candidates}

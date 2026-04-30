"""Tests for ``mdwiki.embeddings`` — sqlite-storable vectors + cosine ANN."""

from __future__ import annotations

import pytest

from mdwiki.embeddings import (
    cosine_similarity,
    deserialize,
    find_top_k,
    serialize,
)


@pytest.mark.unit
def test_serialize_deserialize_round_trip() -> None:
    original = [0.1, -0.2, 0.3, 0.4, -0.5]
    blob = serialize(original)
    restored = deserialize(blob)
    assert pytest.approx(restored, rel=1e-5) == original


@pytest.mark.unit
def test_serialize_returns_bytes() -> None:
    blob = serialize([0.1, 0.2, 0.3])
    assert isinstance(blob, bytes)


@pytest.mark.unit
def test_cosine_similarity_identical_vectors_is_one() -> None:
    a = [1.0, 2.0, 3.0]
    assert cosine_similarity(a, a) == pytest.approx(1.0, rel=1e-5)


@pytest.mark.unit
def test_cosine_similarity_orthogonal_is_zero() -> None:
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert cosine_similarity(a, b) == pytest.approx(0.0, abs=1e-5)


@pytest.mark.unit
def test_cosine_similarity_opposite_is_minus_one() -> None:
    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    assert cosine_similarity(a, b) == pytest.approx(-1.0, rel=1e-5)


@pytest.mark.unit
def test_find_top_k_returns_highest_similarity_first() -> None:
    query = [1.0, 0.0, 0.0]
    candidates = {
        "page-A": [0.9, 0.1, 0.0],   # high similarity
        "page-B": [0.1, 1.0, 0.0],   # low
        "page-C": [0.95, 0.0, 0.05], # higher than A
    }
    result = find_top_k(query, candidates, k=2)
    assert len(result) == 2
    assert result[0][0] == "page-C"
    assert result[1][0] == "page-A"


@pytest.mark.unit
def test_find_top_k_with_empty_candidates_returns_empty() -> None:
    assert find_top_k([1.0, 0.0], {}, k=5) == []


@pytest.mark.unit
def test_find_top_k_caps_at_available_candidates() -> None:
    query = [1.0, 0.0]
    candidates = {"a": [1.0, 0.0], "b": [0.0, 1.0]}
    result = find_top_k(query, candidates, k=10)
    assert len(result) == 2


@pytest.mark.unit
def test_find_top_k_excludes_negative_similarity_below_threshold() -> None:
    query = [1.0, 0.0]
    candidates = {
        "good": [0.9, 0.1],
        "opposite": [-1.0, 0.0],
    }
    result = find_top_k(query, candidates, k=10, min_similarity=0.0)
    paths = [r[0] for r in result]
    assert "opposite" not in paths
    assert "good" in paths


@pytest.mark.unit
def test_serialize_uses_float32_for_storage_compactness() -> None:
    blob = serialize([0.1] * 384)  # MiniLM dimension
    assert len(blob) == 384 * 4  # 4 bytes per float32

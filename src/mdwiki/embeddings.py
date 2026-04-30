"""Sqlite-storable vector helpers + cosine ANN.

Embeddings live in ``pages.embedding`` as ``BLOB`` (float32 bytes). At ingest
time we embed the source's sections, gather the existing pages' vectors, and
return the top-k most cosine-similar candidates for the LLM to consider.

We deliberately avoid pulling in a vector index (faiss, hnswlib) — at ~hundreds
of pages, brute-force cosine over numpy is fast enough and keeps the dependency
surface tiny.
"""

from __future__ import annotations

import numpy as np


def serialize(vector: list[float]) -> bytes:
    """Pack a vector as float32 bytes for sqlite BLOB storage."""
    return np.asarray(vector, dtype=np.float32).tobytes()


def deserialize(blob: bytes) -> list[float]:
    """Unpack a float32 bytes BLOB back into a Python list."""
    return np.frombuffer(blob, dtype=np.float32).tolist()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length vectors. Returns 0.0 if either is the zero vector."""
    arr_a = np.asarray(a, dtype=np.float32)
    arr_b = np.asarray(b, dtype=np.float32)
    norm_a = float(np.linalg.norm(arr_a))
    norm_b = float(np.linalg.norm(arr_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return float(np.dot(arr_a, arr_b) / (norm_a * norm_b))


def find_top_k(
    query: list[float],
    candidates: dict[str, list[float]],
    *,
    k: int,
    min_similarity: float = -1.0,
) -> list[tuple[str, float]]:
    """Return the top-``k`` ``(key, similarity)`` pairs from ``candidates``, descending.

    Parameters
    ----------
    query : list[float]
        The vector to compare against each candidate.
    candidates : dict[str, list[float]]
        Mapping of identifier → vector; identifiers are returned in the result.
    k : int
        Maximum number of results to return.
    min_similarity : float, optional
        Drop any candidate whose similarity is below this threshold (default
        ``-1.0`` = keep all).
    """
    if not candidates:
        return []
    scored = [(key, cosine_similarity(query, vec)) for key, vec in candidates.items()]
    scored = [pair for pair in scored if pair[1] >= min_similarity]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:k]

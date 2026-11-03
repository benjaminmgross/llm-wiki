"""Standing-rule context and bounded pinned-page retrieval."""

from __future__ import annotations

import math
import tomllib
from pathlib import Path

from mdwiki.discover import WIKI_DIR_NAME
from mdwiki.embeddings import cosine_similarity, deserialize
from mdwiki.state import connect


def append_pinned_candidates(*, wiki_root: Path, query_vectors: list[list[float]], selected_paths: list[str]) -> list[str]:
    """Append relevant embedded pins without disturbing ordinary search results."""
    selected = list(dict.fromkeys(selected_paths))
    if not query_vectors:
        return selected
    config_path = wiki_root / WIKI_DIR_NAME / "config.toml"
    config = tomllib.loads(config_path.read_text()).get("capture", {})
    minimum = config.get("pin_min_similarity", 0.20)
    maximum = config.get("pin_max_candidates", 4)
    if isinstance(minimum, bool) or not isinstance(minimum, (int, float)) or not math.isfinite(minimum) or not -1 <= minimum <= 1:
        raise ValueError("[capture].pin_min_similarity must be a finite number between -1 and 1.")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
        raise ValueError("[capture].pin_max_candidates must be a nonnegative integer.")
    if maximum == 0:
        return selected
    with connect(wiki_root / WIKI_DIR_NAME / "state.db") as conn:
        rows = conn.execute("SELECT path, embedding FROM pages WHERE pinned = 1 AND embedding IS NOT NULL ORDER BY path").fetchall()
    scores: list[tuple[str, float]] = []
    for row in rows:
        path = row["path"]
        if path in selected or not (wiki_root / path).is_file():
            continue
        vector = deserialize(row["embedding"])
        score = max(cosine_similarity(query, vector) for query in query_vectors)
        if score >= minimum:
            scores.append((path, score))
    scores.sort(key=lambda item: (-item[1], item[0]))
    return selected + [path for path, _score in scores[:maximum]]


def active_rule_context(*, wiki_root: Path) -> str:
    """Read complete generated rules, including rules without embeddings."""
    path = wiki_root / "wiki" / "standing-rules.md"
    return path.read_text() if path.is_file() else ""

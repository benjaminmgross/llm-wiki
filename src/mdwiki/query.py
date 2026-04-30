"""Query the wiki with a natural-language question.

Reads ``wiki/index.md`` (the LLM's entry point per Karpathy), ANN-searches
``pages.embedding`` for the top-k most relevant pages, and asks the LLM to
synthesize a cited answer drawing from those pages. With ``--file`` the answer
is written as ``wiki/syntheses/<slug>.md`` through ``IngestTransaction`` — this
is the **primary path syntheses are produced** in v1.0.0 (channel A).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mdwiki.discover import WIKI_DIR_NAME, find_wiki
from mdwiki.embedder import Embedder
from mdwiki.embeddings import deserialize, find_top_k, serialize
from mdwiki.index import build_index
from mdwiki.llm import build_provider_from_config
from mdwiki.llm.anthropic import OutputTruncatedError
from mdwiki.llm.base import Message, Provider
from mdwiki.prompts import QUERY_SYSTEM_PROMPT, build_query_user_prompt
from mdwiki.state import connect
from mdwiki.transaction import IngestTransaction

QUERY_CANDIDATES_TOP_K: int = 8
QUERY_MIN_SIMILARITY: float = 0.0
QUERY_MAX_TOKENS_DEFAULT: int = 8000


class QueryError(Exception):
    """Raised when query cannot proceed (no wiki, LLM truncation, etc.)."""


@dataclass(frozen=True)
class QueryResult:
    """Outcome of a ``query_wiki`` call."""

    question: str
    answer: str
    cited_pages: tuple[str, ...]
    filed_path: str | None


def query_wiki(
    wiki_root: Path,
    question: str,
    *,
    file: bool = False,
    yes: bool = False,
    confirm: Callable[[str], bool] | None = None,
    provider: Provider | None = None,
    embedder: Embedder | None = None,
    max_tokens: int = QUERY_MAX_TOKENS_DEFAULT,
) -> QueryResult:
    """Answer ``question`` from the wiki; optionally file the answer as a synthesis page.

    Parameters
    ----------
    wiki_root : Path
        Directory containing ``.mdwiki/``.
    question : str
        The natural-language question to answer.
    file : bool, optional
        If True, write the answer as a synthesis page after confirmation.
    yes : bool, optional
        Skip the confirmation prompt when ``file`` is True.
    confirm : callable, optional
        Function ``(answer_text) -> bool`` controlling whether to file. Used by tests.
    provider : Provider, optional
        Pre-built LLM provider; defaults to one built from ``[llm]`` config.
    embedder : Embedder, optional
        Pre-built embedder; defaults to a lazy module-level singleton.
    max_tokens : int, optional
        Cap on answer length (default 8000).

    Raises
    ------
    QueryError
        If no wiki is reachable from ``wiki_root`` or the LLM truncated mid-answer.
    """
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    if not db_path.is_file():
        raise QueryError(f"No wiki at {wiki_root}/{WIKI_DIR_NAME}/. Run `mdwiki init` first.")

    schema_text = (wiki_root / WIKI_DIR_NAME / "schema.md").read_text()
    index_path = wiki_root / "wiki" / "index.md"
    index_text = index_path.read_text() if index_path.is_file() else ""

    embedder = embedder or _get_default_embedder()
    question_vec = embedder.embed_text(question)
    candidate_pages = _find_candidate_pages_for_query(wiki_root=wiki_root, query_vec=question_vec)

    user_prompt = build_query_user_prompt(
        question=question,
        index_text=index_text,
        candidate_pages=candidate_pages,
        schema_text=schema_text,
    )

    provider = provider or build_provider_from_config(wiki_root)
    try:
        response = provider.complete(
            system=QUERY_SYSTEM_PROMPT,
            messages=[Message(role="user", content=user_prompt)],
            max_tokens=max_tokens,
        )
    except OutputTruncatedError as exc:
        raise QueryError(str(exc)) from exc

    answer = response.text.strip()
    cited_pages = _extract_cited_pages(answer)

    filed_path: str | None = None
    if file:
        if not yes:
            chooser = confirm or _terminal_confirm_file
            if not chooser(answer):
                return QueryResult(question=question, answer=answer, cited_pages=cited_pages, filed_path=None)

        filed_path = _file_as_synthesis(
            wiki_root=wiki_root,
            question=question,
            answer=answer,
            cited_pages=cited_pages,
            embedder=embedder,
        )

    return QueryResult(question=question, answer=answer, cited_pages=cited_pages, filed_path=filed_path)


def slugify_question(question: str, *, max_chars: int = 60) -> str:
    """Turn a question into a filesystem-safe kebab-case slug for synthesis filenames."""
    slug = re.sub(r"[^a-z0-9]+", "-", question.lower()).strip("-")
    if not slug:
        return "untitled-query"
    return slug[:max_chars].rstrip("-")


def _find_candidate_pages_for_query(*, wiki_root: Path, query_vec: list[float]) -> list[dict[str, str]]:
    """ANN search for the top-k pages most similar to the question."""
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    with connect(db_path) as conn:
        rows = conn.execute("SELECT path, embedding FROM pages WHERE embedding IS NOT NULL").fetchall()
    if not rows:
        return []
    page_vectors = {row["path"]: deserialize(row["embedding"]) for row in rows}
    ranked = find_top_k(query_vec, page_vectors, k=QUERY_CANDIDATES_TOP_K, min_similarity=QUERY_MIN_SIMILARITY)

    candidates: list[dict[str, str]] = []
    for path, _score in ranked:
        full = wiki_root / path
        if full.is_file():
            candidates.append({"path": path, "content": full.read_text()})
    return candidates


_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+\.md)\)")


def _extract_cited_pages(answer: str) -> tuple[str, ...]:
    """Extract every wiki page referenced via markdown link in the answer.

    Resolves relative paths like ``../concepts/foo.md`` to ``wiki/concepts/foo.md``.
    """
    raw_links = _LINK_RE.findall(answer)
    resolved: list[str] = []
    for link in raw_links:
        if link.startswith("wiki/"):
            normalized = link
        elif link.startswith("../"):
            normalized = "wiki/" + link.removeprefix("../")
        else:
            normalized = "wiki/" + link.lstrip("./")
        if normalized not in resolved:
            resolved.append(normalized)
    return tuple(resolved)


def _file_as_synthesis(
    *,
    wiki_root: Path,
    question: str,
    answer: str,
    cited_pages: tuple[str, ...],
    embedder: Embedder,
) -> str:
    """Write the answer as a synthesis page through ``IngestTransaction``."""
    slug = slugify_question(question)
    rel_path = f"wiki/syntheses/{slug}.md"
    target = wiki_root / rel_path

    body = _wrap_synthesis_body(question=question, answer=answer)
    summary = f"query --file: {question[:80]}"

    with IngestTransaction(wiki_root=wiki_root, source_id=None, summary=summary) as tx:
        tx.write_file(target, body)
        embedding_blob = serialize(embedder.embed_text(body))
        tx.upsert_page(path=rel_path, kind="synthesis", embedding=embedding_blob, last_touched_at=_now_ts())
        tx.write_file(wiki_root / "wiki" / "index.md", build_index(wiki_root))

    return rel_path


def _wrap_synthesis_body(*, question: str, answer: str) -> str:
    """Add a small header noting the question this synthesis answered."""
    return f"# {question.rstrip('?')}\n\n_Filed from `mdwiki query --file`._\n\n{answer}\n"


def _terminal_confirm_file(answer: str) -> bool:
    print(f"\n--- proposed synthesis page ---\n\n{answer}\n\n--- end ---\n")
    return input("File this answer as a synthesis page? [y/N] ").strip().lower() == "y"


def _now_ts() -> float:
    import time

    return time.time()


class _LazyEmbedder:
    """Module-singleton for the embedder (mirrors the pattern in ingest.py)."""

    def __init__(self) -> None:
        self._instance: Embedder | None = None

    def get(self) -> Embedder:
        if self._instance is None:
            self._instance = Embedder()
        return self._instance


_DEFAULT = _LazyEmbedder()


def _get_default_embedder() -> Embedder:
    return _DEFAULT.get()


# Keep ``find_wiki`` import — tests construct ``wiki_root`` themselves but the CLI uses it.
_ = find_wiki

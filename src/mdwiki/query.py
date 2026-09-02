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

from mdwiki.discover import WIKI_DIR_NAME
from mdwiki.embedder import Embedder, get_default_embedder
from mdwiki.embeddings import deserialize, find_top_k, serialize
from mdwiki.frontmatter import apply_page_metadata
from mdwiki.llm import build_provider_from_config
from mdwiki.llm.anthropic import OutputTruncatedError
from mdwiki.llm.base import Message, Provider
from mdwiki.prompts import QUERY_SYSTEM_PROMPT, build_query_user_prompt
from mdwiki.state import connect
from mdwiki.transaction import IngestTransaction

QUERY_CANDIDATES_TOP_K: int = 8
QUERY_MIN_SIMILARITY: float = 0.0
QUERY_MAX_TOKENS_DEFAULT: int = 8000
FILE_SUGGESTION_MIN_CITED_PAGES: int = 3
NO_COVERAGE_PREFIX: str = "NO_COVERAGE:"
_CONFIDENCE_RE = re.compile(r"^\s*\**Confidence\**\s*:\s*\**(high|medium|low)\**", re.IGNORECASE | re.MULTILINE)


class QueryError(Exception):
    """Raised when query cannot proceed (no wiki, LLM truncation, etc.)."""


@dataclass(frozen=True)
class QueryResult:
    """Outcome of a ``query_wiki`` call."""

    question: str
    answer: str
    cited_pages: tuple[str, ...]
    filed_path: str | None
    confidence: str | None = None
    file_suggested: bool = False
    stub_path: str | None = None
    no_coverage: bool = False


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
    stub: bool = False,
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
    stub : bool, optional
        When the model reports ``NO_COVERAGE:``, write a stub concept page
        tagged ``stub, needs-sources`` so the gap is visible in the concept
        table and lint.

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

    embedder = embedder or get_default_embedder()
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
    confidence = _extract_confidence(answer)
    no_coverage = answer.startswith(NO_COVERAGE_PREFIX)

    if no_coverage:
        stub_path = _file_stub(wiki_root=wiki_root, question=question, reason=answer, embedder=embedder) if stub else None
        return QueryResult(
            question=question,
            answer=answer,
            cited_pages=(),
            filed_path=None,
            confidence=confidence,
            file_suggested=False,
            stub_path=stub_path,
            no_coverage=True,
        )

    file_suggested = not file and len(cited_pages) >= FILE_SUGGESTION_MIN_CITED_PAGES
    filed_path: str | None = None
    if file:
        if not yes:
            chooser = confirm or _terminal_confirm_file
            if not chooser(answer):
                return QueryResult(question=question, answer=answer, cited_pages=cited_pages, filed_path=None, confidence=confidence)

        filed_path = _file_as_synthesis(
            wiki_root=wiki_root,
            question=question,
            answer=answer,
            cited_pages=cited_pages,
            embedder=embedder,
        )

    return QueryResult(
        question=question,
        answer=answer,
        cited_pages=cited_pages,
        filed_path=filed_path,
        confidence=confidence,
        file_suggested=file_suggested,
    )


def _extract_confidence(answer: str) -> str | None:
    """Return ``high``/``medium``/``low`` from the answer's trailing ``Confidence:`` line, or ``None``."""
    matches = _CONFIDENCE_RE.findall(answer)
    return matches[-1].lower() if matches else None


def _file_stub(*, wiki_root: Path, question: str, reason: str, embedder: Embedder) -> str:
    """Write a ``stub, needs-sources`` concept page recording a question the wiki cannot answer."""
    slug = slugify_question(question)
    rel_path = f"wiki/concepts/{slug}.md"
    target = wiki_root / rel_path
    if target.exists():
        return rel_path
    detail = reason.removeprefix(NO_COVERAGE_PREFIX).strip() or "the wiki has no pages covering this question"
    body = (
        f"# {question.rstrip('?')}\n\n"
        f"_Stub created by `mdwiki query --stub`: the wiki could not answer \"{question}\"._\n\n"
        f"Gap: {detail}\n\n"
        "Ingest a source that covers this and the next plan should update this page; lint keeps it visible until then.\n"
    )
    page = apply_page_metadata(body, path=rel_path, kind="concept", source_ids=())
    page = page.replace("tags: [concept]", "tags: [stub, needs-sources]", 1)
    with IngestTransaction(wiki_root=wiki_root, source_id=None, summary=f"query --stub: {question[:80]}", event_kind="query-stub") as tx:
        tx.write_file(target, page)
        tx.upsert_page(path=rel_path, kind="concept", embedding=serialize(embedder.embed_text(page)), last_touched_at=_now_ts())
    return rel_path


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

    body = apply_page_metadata(_wrap_synthesis_body(question=question, answer=answer), path=rel_path, kind="synthesis", source_ids=())
    summary = f"query --file: {question[:80]}"

    with IngestTransaction(wiki_root=wiki_root, source_id=None, summary=summary, event_kind="query-filed") as tx:
        tx.write_file(target, body)
        embedding_blob = serialize(embedder.embed_text(body))
        tx.upsert_page(path=rel_path, kind="synthesis", embedding=embedding_blob, last_touched_at=_now_ts())

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



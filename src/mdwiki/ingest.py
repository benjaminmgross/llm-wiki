"""Orchestrate one source through the ingest pipeline.

Pulls together the chunker, provider, plan parser, quote verifier, transaction,
and interactive UX into a single ``ingest_source`` entry point. The CLI handler
in ``cli.py`` is a thin shell around this function.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import anthropic

from mdwiki.chunker import MarkdownChunker
from mdwiki.discover import WIKI_DIR_NAME
from mdwiki.embedder import Embedder
from mdwiki.embeddings import deserialize, find_top_k, serialize
from mdwiki.index import build_index
from mdwiki.llm import build_provider_from_config
from mdwiki.llm.anthropic import OutputTruncatedError
from mdwiki.llm.base import Message, Provider
from mdwiki.plan import Plan, PlanValidationError, parse_plan
from mdwiki.prompts import INGEST_SYSTEM_PROMPT, build_ingest_user_prompt
from mdwiki.quote import verify_plan
from mdwiki.state import connect
from mdwiki.transaction import IngestTransaction

CANDIDATES_PER_SECTION: int = 3
MAX_CANDIDATES_TOTAL: int = 8
MIN_CANDIDATE_SIMILARITY: float = 0.25


class IngestError(Exception):
    """Raised when ingest cannot proceed (unknown source, bad quotes, parse failure)."""


@dataclass(frozen=True)
class IngestResult:
    """Outcome of an ``ingest_source`` call."""

    source_id: str | None
    applied: bool
    message: str
    plan: Plan | None = None


def ingest_source(
    wiki_root: Path,
    source_id_or_path: str,
    *,
    yes: bool = False,
    provider: Provider | None = None,
    confirm: Callable[[Plan], bool] | None = None,
    max_tokens: int = 16000,
    embedder: Embedder | None = None,
) -> IngestResult:
    """Run the full ingest pipeline for one source.

    Parameters
    ----------
    wiki_root : Path
        Folder containing ``.mdwiki/``.
    source_id_or_path : str
        Either a 12-char source id (or unique prefix) or the original_path of a registered source.
    yes : bool, optional
        Skip the interactive confirmation; apply the plan if quotes verify.
    provider : Provider, optional
        Pre-built provider; defaults to one built from ``[llm]`` config.
    confirm : callable, optional
        Function that takes the parsed ``Plan`` and returns True to apply, False to abort.
        Used by tests; ignored if ``yes`` is True. Defaults to a terminal y/N prompt.
    max_tokens : int, optional
        Cap on LLM output tokens (default 4096).

    Raises
    ------
    IngestError
        If the source is unknown, the LLM response is unparseable, or quote verification fails.
    """
    source_row = _resolve_source(wiki_root, source_id_or_path)
    if source_row["status"] == "ingested":
        return IngestResult(
            source_id=source_row["id"],
            applied=False,
            message=f"Source {source_row['original_path']} is already ingested. Run `mdwiki undo` first if you want to re-ingest.",
        )

    raw_path = wiki_root / source_row["raw_path"]
    chunker = MarkdownChunker(min_section_words=1)
    sections = _chunk_or_whole(raw_path, source_path=source_row["original_path"], chunker=chunker)
    section_ids = {s["section_id"] for s in sections}
    source_text = raw_path.read_text()

    schema_text = (wiki_root / WIKI_DIR_NAME / "schema.md").read_text()
    recent_log = _recent_log_entries(wiki_root, limit=10)

    provider = provider or build_provider_from_config(wiki_root)
    embedder = embedder or _DEFAULT_EMBEDDER.get()
    section_vectors = [embedder.embed_text(s["content"]) for s in sections] if sections else []
    candidate_pages = _find_candidate_pages(wiki_root=wiki_root, section_vectors=section_vectors)

    user_prompt = build_ingest_user_prompt(
        source_path=source_row["original_path"],
        sections=sections,
        candidate_pages=candidate_pages,
        schema_text=schema_text,
        recent_log_entries=recent_log,
    )

    try:
        response = provider.complete(
            system=INGEST_SYSTEM_PROMPT,
            messages=[Message(role="user", content=user_prompt)],
            max_tokens=max_tokens,
        )
    except OutputTruncatedError as exc:
        raise IngestError(str(exc)) from exc

    try:
        plan = parse_plan(response.text)
    except PlanValidationError as exc:
        raise IngestError(f"LLM returned an unparseable plan: {exc}") from exc

    verification = verify_plan(plan, source_text=source_text, section_ids=section_ids)
    if not verification.valid:
        details = "\n  ".join(err.message for err in verification.errors)
        raise IngestError(f"Quote verification failed:\n  {details}")

    if plan.is_empty():
        with IngestTransaction(
            wiki_root=wiki_root,
            source_id=source_row["id"],
            summary=f"{plan.verdict}: {source_row['original_path']} — {plan.rationale}",
        ):
            pass
        return IngestResult(
            source_id=source_row["id"],
            applied=True,
            message=f"Verdict: {plan.verdict}. {plan.rationale} (no edits applied)",
            plan=plan,
        )

    if not yes:
        chooser = confirm or _terminal_confirm
        if not chooser(plan):
            return IngestResult(
                source_id=source_row["id"],
                applied=False,
                message="rejected by user — no changes made",
                plan=plan,
            )

    summary = f"ingest {source_row['original_path']} → {len(plan.updates)} update(s), {len(plan.new_pages)} new page(s)"
    with IngestTransaction(wiki_root=wiki_root, source_id=source_row["id"], summary=summary) as tx:
        page_embeddings: dict[str, bytes] = {}
        for new_page in plan.new_pages:
            tx.write_file(wiki_root / new_page.path, new_page.content)
            page_embeddings[new_page.path] = serialize(embedder.embed_text(new_page.content))
        for update in plan.updates:
            # update.content is the COMPLETE revised page (per the prompt
            # contract) — write it verbatim. Previous releases concatenated
            # update.content onto the existing file, which silently violated
            # the spec and made wiki pages grow without bound.
            full_target = wiki_root / update.page
            tx.write_file(full_target, update.content)
            page_embeddings[update.page] = serialize(embedder.embed_text(update.content))
        _apply_pages_and_backrefs(tx=tx, plan=plan, embeddings=page_embeddings, source_id=source_row["id"])
        tx.write_file(wiki_root / "wiki" / "index.md", build_index(wiki_root))

    return IngestResult(source_id=source_row["id"], applied=True, message=summary, plan=plan)


def ingest_many(
    wiki_root: Path,
    scope: Literal["all", "pending"] = "pending",
    *,
    yes: bool = True,
    provider: Provider | None = None,
    embedder: Embedder | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
    on_failure: Callable[[str, Exception], None] | None = None,
) -> list[IngestResult]:
    """Ingest every source in the chosen ``scope`` sequentially.

    Resilient: a failure on one source (quote verification, LLM truncation,
    transient network/API error) is collected as a returned ``IngestResult``
    with ``applied=False`` and processing continues with the next source.
    Each source is its own ``IngestTransaction`` — interrupting between
    sources is safe, and the recovery path is to re-run with ``scope="pending"``
    (sources whose tx never committed are still ``pending``). v1.0.0 does
    NOT carry a checkpoint file: on partial bulk runs, ``--pending`` is the
    resume mechanism.

    Parameters
    ----------
    scope : "all" | "pending"
        ``"pending"`` skips sources already marked ingested; ``"all"`` re-ingests
        everything (intended for forcing fresh analysis after a schema bump).
    yes : bool, optional
        Default ``True`` — bulk mode shouldn't prompt per source.
    on_progress : callable, optional
        Called as ``(index, total, original_path)`` before each source.
    on_failure : callable, optional
        Called as ``(original_path, exception)`` when a single ingest raises.
    """
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    where = "" if scope == "all" else "WHERE status = 'pending'"
    with connect(db_path) as conn:
        rows = conn.execute(f"SELECT id, original_path FROM sources {where} ORDER BY original_path").fetchall()
    targets = [(row["id"], row["original_path"]) for row in rows]

    if provider is None:
        provider = build_provider_from_config(wiki_root)
    if embedder is None:
        embedder = _DEFAULT_EMBEDDER.get()

    results: list[IngestResult] = []
    for index, (source_id, original_path) in enumerate(targets, start=1):
        if on_progress is not None:
            on_progress(index, len(targets), original_path)
        try:
            result = ingest_source(
                wiki_root,
                source_id,
                yes=yes,
                provider=provider,
                embedder=embedder,
            )
            results.append(result)
        except (IngestError, anthropic.APIError) as exc:
            # IngestError covers logical failures (bad quotes, parse errors).
            # anthropic.APIError covers SDK-level transient/permanent failures
            # (rate limit, overloaded, server error) that the SDK's max_retries
            # already exhausted — surface and continue rather than aborting
            # the whole bulk run on one bad source.
            if on_failure is not None:
                on_failure(original_path, exc)
            results.append(
                IngestResult(source_id=source_id, applied=False, message=f"failed: {exc}", plan=None)
            )
    return results


def _resolve_source(wiki_root: Path, source_id_or_path: str) -> dict[str, Any]:
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    candidates = _candidate_paths(source_id_or_path, wiki_root=wiki_root)

    with connect(db_path) as conn:
        for candidate in candidates:
            row = conn.execute(
                "SELECT id, original_path, raw_path, status FROM sources WHERE original_path = ?",
                (candidate,),
            ).fetchone()
            if row is not None:
                return dict(row)

        rows = conn.execute(
            "SELECT id, original_path, raw_path, status FROM sources WHERE id LIKE ?",
            (source_id_or_path + "%",),
        ).fetchall()
        if len(rows) == 1:
            return dict(rows[0])
        if len(rows) > 1:
            ambiguous = ", ".join(r["id"] for r in rows[:5])
            raise IngestError(f"Ambiguous prefix {source_id_or_path!r} matches {len(rows)} sources: {ambiguous}")

        suggestions = _suggest_sources(conn, query=source_id_or_path, limit=5)

    base_msg = f"Source {source_id_or_path!r} is not registered."
    if suggestions:
        listed = "\n  ".join(suggestions)
        raise IngestError(f"{base_msg} Did you mean one of:\n  {listed}\n\nRun `mdwiki status` to list all sources.")
    raise IngestError(f"{base_msg} Run `mdwiki status` to list known sources.")


def _candidate_paths(raw: str, *, wiki_root: Path) -> list[str]:
    """Generate normalized candidates to try against ``sources.original_path``."""
    seen: list[str] = []

    def add(value: str) -> None:
        if value and value not in seen:
            seen.append(value)

    add(raw)
    if raw.startswith("./"):
        add(raw[2:])
    if "\\ " in raw:
        unescaped = raw.replace("\\ ", " ")
        add(unescaped)
        if unescaped.startswith("./"):
            add(unescaped[2:])

    candidate_path = Path(seen[-1]) if seen else None
    if candidate_path is not None and candidate_path.is_file():
        try:
            rel = candidate_path.resolve().relative_to(wiki_root.resolve())
        except ValueError:
            rel = None
        if rel is not None:
            add(rel.as_posix())

    return seen


def _suggest_sources(conn: Any, *, query: str, limit: int) -> list[str]:
    """Return up to ``limit`` original_paths that share a token with the query."""
    tokens = [t for t in query.replace("\\ ", " ").replace("/", " ").split() if len(t) >= 2]
    if not tokens:
        return []
    where_parts = " OR ".join(["original_path LIKE ?"] * len(tokens))
    params = tuple(f"%{t}%" for t in tokens)
    rows = conn.execute(
        f"SELECT DISTINCT original_path FROM sources WHERE {where_parts} ORDER BY original_path LIMIT ?",
        (*params, limit),
    ).fetchall()
    return [row["original_path"] for row in rows]


def _chunk_or_whole(raw_path: Path, *, source_path: str, chunker: MarkdownChunker) -> list[dict[str, Any]]:
    sections = chunker.chunk_file(raw_path)
    if sections:
        rewritten: list[dict[str, Any]] = []
        for s in sections:
            d = dict(s)
            d["section_id"] = f"{source_path}/{s['heading']}"
            rewritten.append(d)
        return rewritten
    content = raw_path.read_text()
    return [
        {
            "section_id": source_path,
            "heading": "(whole file)",
            "content": content,
        }
    ]


def _recent_log_entries(wiki_root: Path, *, limit: int) -> list[str]:
    log_path = wiki_root / "wiki" / "log.md"
    if not log_path.is_file():
        return []
    lines = [line.strip() for line in log_path.read_text().splitlines() if line.strip()]
    return lines[-limit:]


def _apply_pages_and_backrefs(*, tx: IngestTransaction, plan: Plan, embeddings: dict[str, bytes], source_id: str) -> None:
    """Insert/update pages and insert backrefs through the transaction's undo-aware helpers."""
    import time

    now = time.time()
    for new_page in plan.new_pages:
        tx.upsert_page(
            path=new_page.path, kind=new_page.kind, embedding=embeddings.get(new_page.path), last_touched_at=now
        )
    for update in plan.updates:
        tx.upsert_page(
            path=update.page,
            kind=_infer_kind(update.page),
            embedding=embeddings.get(update.page),
            last_touched_at=now,
        )
    for update in plan.updates:
        for claim in update.claims:
            tx.insert_backref(
                page_path=update.page, source_id=source_id, section_anchor=claim.source_section_id, quote=claim.quote
            )
    for new_page in plan.new_pages:
        for claim in new_page.claims:
            tx.insert_backref(
                page_path=new_page.path, source_id=source_id, section_anchor=claim.source_section_id, quote=claim.quote
            )


def _find_candidate_pages(*, wiki_root: Path, section_vectors: list[list[float]]) -> list[dict[str, str]]:
    """ANN-search over ``pages.embedding`` to find pages most similar to the source's sections.

    Returns a list of ``{path, content}`` dicts, deduplicated, capped at
    ``MAX_CANDIDATES_TOTAL``. Empty when the wiki has no embedded pages yet
    (first ingest into a fresh wiki).
    """
    if not section_vectors:
        return []
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    with connect(db_path) as conn:
        rows = conn.execute("SELECT path, embedding FROM pages WHERE embedding IS NOT NULL").fetchall()
    if not rows:
        return []
    page_vectors = {row["path"]: deserialize(row["embedding"]) for row in rows}

    accumulated: dict[str, float] = {}
    for vec in section_vectors:
        for path, score in find_top_k(
            vec, page_vectors, k=CANDIDATES_PER_SECTION, min_similarity=MIN_CANDIDATE_SIMILARITY
        ):
            accumulated[path] = max(accumulated.get(path, 0.0), score)

    ranked_paths = sorted(accumulated, key=lambda p: accumulated[p], reverse=True)[:MAX_CANDIDATES_TOTAL]

    candidates: list[dict[str, str]] = []
    for path in ranked_paths:
        full = wiki_root / path
        if full.is_file():
            candidates.append({"path": path, "content": full.read_text()})
    return candidates


class _LazyEmbedder:
    """Module-singleton for the embedder; loads the model on first access only."""

    def __init__(self) -> None:
        self._instance: Embedder | None = None

    def get(self) -> Embedder:
        if self._instance is None:
            self._instance = Embedder()
        return self._instance


_DEFAULT_EMBEDDER = _LazyEmbedder()


def _infer_kind(page_path: str) -> str:
    if "/entities/" in page_path:
        return "entity"
    if "/concepts/" in page_path:
        return "concept"
    if "/syntheses/" in page_path:
        return "synthesis"
    return "concept"


def _terminal_confirm(plan: Plan) -> bool:
    print(f"\nLLM proposes: verdict={plan.verdict}")
    print(f"  rationale: {plan.rationale}")
    print(f"  {len(plan.updates)} update(s), {len(plan.new_pages)} new page(s), {len(plan.cross_refs)} cross-ref(s)")
    for new_page in plan.new_pages:
        print(f"  + new {new_page.kind}: {new_page.path} ({len(new_page.claims)} claim(s))")
    for update in plan.updates:
        print(f"  ~ update: {update.page} ({len(update.claims)} claim(s))")
    answer = input("\nApply? [y/N] ").strip().lower()
    return answer == "y"



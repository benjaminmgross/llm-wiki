"""Batch-API bootstrap path — submits every pending source as one batch (50% cheaper, ~1h).

The sync path lives in ``ingest.ingest_source`` / ``ingest_many``. This module mirrors
that pipeline but routes the LLM round-trip through ``Provider.batch_complete`` so the
user pays the Anthropic batch discount in exchange for ~1h latency. Quote verification
and ``IngestTransaction`` application are reused unchanged after the batch completes.

Failed entries (rate-limit, validation errors) leave the source as ``pending``; the user
recovers by re-running ``mdwiki init --bootstrap-batch`` (or the sync ``--bootstrap``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mdwiki.chunker import MarkdownChunker
from mdwiki.discover import WIKI_DIR_NAME
from mdwiki.embedder import Embedder, get_default_embedder
from mdwiki.embeddings import serialize
from mdwiki.index import build_index
from mdwiki.ingest import (
    _apply_pages_and_backrefs,
    _chunk_or_whole,
    _find_candidate_pages,
    _recent_log_entries,
)
from mdwiki.llm import build_provider_from_config
from mdwiki.llm.base import (
    BatchCostEstimate,
    BatchRequest,
    Message,
    Provider,
)
from mdwiki.ingest_tool import INGEST_TOOL_CHOICE, INGEST_TOOL_DEFINITION
from mdwiki.plan import PlanValidationError, allowed_kinds_for_wiki, parse_plan, parse_plan_dict
from mdwiki.prompts import INGEST_SYSTEM_PROMPT_TOOL_USE
from mdwiki.prompts import INGEST_SYSTEM_PROMPT, build_ingest_user_prompt
from mdwiki.quote import min_quote_words_for_wiki, quote_normalize_mode_for_wiki, verify_plan
from mdwiki.state import connect
from mdwiki.transaction import IngestTransaction


@dataclass(frozen=True)
class BootstrapResult:
    """Outcome of a ``bootstrap_batch`` call.

    Parameters
    ----------
    submitted : int
        Number of sources sent in the batch.
    applied : int
        Number whose plans verified and were applied via IngestTransaction.
    failed : int
        Number that came back with errors (rate-limited, invalid response, etc.).
        These remain ``pending`` for retry.
    skipped : int
        Number whose plans verified but had verdict-only / empty plans.
    cost_estimate : BatchCostEstimate
        Pre-submission cost estimate (NOT actual billed cost).
    batch_id : str
        Anthropic batch id, useful for diagnostics.
    """

    submitted: int
    applied: int
    failed: int
    skipped: int
    cost_estimate: BatchCostEstimate
    batch_id: str


@dataclass
class _RequestContext:
    """Per-source data the post-batch step needs to verify + apply a plan."""

    source_row: dict[str, Any]
    source_text: str
    section_ids: set[str]


def bootstrap_batch(
    wiki_root: Path,
    *,
    yes: bool = False,
    provider: Provider | None = None,
    embedder: Embedder | None = None,
    poll_interval: float = 60.0,
    confirm: Callable[[BatchCostEstimate], bool] | None = None,
    on_status: Callable[[str, int, int], None] | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
    on_batch_id: Callable[[str], None] | None = None,
) -> BootstrapResult:
    """Submit every pending source as one batch and apply each successful result.

    Parameters
    ----------
    wiki_root : Path
        Folder containing ``.mdwiki/``.
    yes : bool
        Skip the cost-estimate confirm prompt.
    provider, embedder : optional
        Injection seams; default to ``build_provider_from_config(wiki_root)`` and
        ``get_default_embedder()``.
    poll_interval : float
        Seconds between batch-status polls. Default 60s.
    confirm : callable
        Called with the cost estimate when ``yes`` is False; return True to submit.
    on_status, on_progress : callable
        Progress callbacks for the CLI to surface polling + per-source application.
    """
    provider = provider or build_provider_from_config(wiki_root)
    embedder = embedder or get_default_embedder()

    pending = _load_pending(wiki_root)
    if not pending:
        return BootstrapResult(
            submitted=0,
            applied=0,
            failed=0,
            skipped=0,
            cost_estimate=BatchCostEstimate(requests=0, input_tokens=0, output_tokens_max=0, usd_total=0.0),
            batch_id="",
        )

    # Anthropic supports constrained-decoding ``tool_use``; route every batch
    # request through the ingest tool so each response is structurally
    # guaranteed to parse. Other providers fall back to free-form JSON.
    use_tool = provider.name == "anthropic"

    requests: list[BatchRequest] = []
    contexts: dict[str, _RequestContext] = {}
    for source_row in pending:
        request, context = _prepare_request(
            wiki_root=wiki_root, source_row=source_row, embedder=embedder, use_tool=use_tool
        )
        requests.append(request)
        contexts[source_row["id"]] = context

    estimate = provider.estimate_batch_cost(requests)
    if not yes:
        chooser = confirm or _terminal_confirm_estimate
        if not chooser(estimate):
            return BootstrapResult(
                submitted=0, applied=0, failed=0, skipped=0, cost_estimate=estimate, batch_id=""
            )

    captured_batch_id: str = ""

    def _capture_batch_id(value: str) -> None:
        nonlocal captured_batch_id
        captured_batch_id = value
        if on_batch_id is not None:
            on_batch_id(value)

    results = provider.batch_complete(
        requests,
        poll_interval=poll_interval,
        on_status=on_status,
        on_batch_id=_capture_batch_id,
    )

    applied = 0
    failed = 0
    skipped = 0
    batch_id = captured_batch_id
    for index, result in enumerate(results, start=1):
        if on_progress is not None:
            on_progress(index, len(results), result.custom_id)
        if result.error is not None:
            failed += 1
            continue
        context = contexts.get(result.custom_id)
        if context is None:
            failed += 1
            continue
        outcome = _apply_one_result(
            wiki_root=wiki_root,
            embedder=embedder,
            response_text=result.text,
            context=context,
            tool_input=result.tool_input,
        )
        if outcome == "applied":
            applied += 1
        elif outcome == "skipped":
            skipped += 1
        else:
            failed += 1

    return BootstrapResult(
        submitted=len(requests),
        applied=applied,
        failed=failed,
        skipped=skipped,
        cost_estimate=estimate,
        batch_id=batch_id,
    )


def _load_pending(wiki_root: Path) -> list[dict[str, Any]]:
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, original_path, raw_path, status FROM sources WHERE status = 'pending' ORDER BY original_path"
        ).fetchall()
    return [dict(row) for row in rows]


def _prepare_request(
    *,
    wiki_root: Path,
    source_row: dict[str, Any],
    embedder: Embedder,
    use_tool: bool,
) -> tuple[BatchRequest, _RequestContext]:
    """Build one ``BatchRequest`` + capture the context needed for post-batch apply.

    When ``use_tool`` is true (Anthropic provider), the request carries the
    ingest ``tool_use`` schema so each batch entry returns a structurally
    valid plan in ``BatchResult.tool_input``.
    """
    raw_path = wiki_root / source_row["raw_path"]
    chunker = MarkdownChunker(min_section_words=1)
    sections = _chunk_or_whole(raw_path, source_path=source_row["original_path"], chunker=chunker)
    section_ids = {s["section_id"] for s in sections}
    source_text = raw_path.read_text()

    schema_text = (wiki_root / WIKI_DIR_NAME / "schema.md").read_text()
    recent_log = _recent_log_entries(wiki_root, limit=10)
    section_vectors = [embedder.embed_text(s["content"]) for s in sections] if sections else []
    candidate_pages = _find_candidate_pages(wiki_root=wiki_root, section_vectors=section_vectors)

    user_prompt = build_ingest_user_prompt(
        source_path=source_row["original_path"],
        sections=sections,
        candidate_pages=candidate_pages,
        schema_text=schema_text,
        recent_log_entries=recent_log,
    )
    request = BatchRequest(
        custom_id=source_row["id"],
        system=INGEST_SYSTEM_PROMPT_TOOL_USE if use_tool else INGEST_SYSTEM_PROMPT,
        messages=[Message(role="user", content=user_prompt)],
        max_tokens=16000,
        tools=[INGEST_TOOL_DEFINITION] if use_tool else None,
        tool_choice=INGEST_TOOL_CHOICE if use_tool else None,
    )
    context = _RequestContext(source_row=source_row, source_text=source_text, section_ids=section_ids)
    return request, context


def _apply_one_result(
    *,
    wiki_root: Path,
    embedder: Embedder,
    response_text: str,
    context: _RequestContext,
    tool_input: dict[str, Any] | None = None,
) -> str:
    """Parse, verify, and apply one batch result. Returns ``"applied"|"skipped"|"failed"``.

    When ``tool_input`` is present (Anthropic ``tool_use`` path), the parsed
    plan is taken directly from the SDK-exposed dict. Otherwise the legacy
    free-form text path is used.
    """
    try:
        # Mirror the sync ingest path: pass profile-aware allowed_kinds so
        # batch results that propose profile-specific page kinds (framework's
        # ``procedure``/``template``, transcripts' ``meeting``/``decision``,
        # initiative's ``workstream``/``owner``, etc.) parse instead of being
        # rejected as "Invalid page kind".
        if tool_input is not None:
            plan = parse_plan_dict(tool_input, allowed_kinds=allowed_kinds_for_wiki(wiki_root))
        else:
            plan = parse_plan(response_text, allowed_kinds=allowed_kinds_for_wiki(wiki_root))
    except PlanValidationError:
        return "failed"

    verification = verify_plan(
        plan,
        source_text=context.source_text,
        section_ids=context.section_ids,
        mode=quote_normalize_mode_for_wiki(wiki_root),
        min_quote_words=min_quote_words_for_wiki(wiki_root),
    )
    if not verification.valid:
        return "failed"

    source_row = context.source_row
    if plan.is_empty():
        with IngestTransaction(
            wiki_root=wiki_root,
            source_id=source_row["id"],
            summary=f"{plan.verdict}: {source_row['original_path']} — {plan.rationale}",
        ):
            pass
        return "skipped"

    summary = (
        f"ingest {source_row['original_path']} → "
        f"{len(plan.updates)} update(s), {len(plan.new_pages)} new page(s) [batch]"
    )
    with IngestTransaction(wiki_root=wiki_root, source_id=source_row["id"], summary=summary) as tx:
        page_embeddings: dict[str, bytes] = {}
        for new_page in plan.new_pages:
            tx.write_file(wiki_root / new_page.path, new_page.content)
            page_embeddings[new_page.path] = serialize(embedder.embed_text(new_page.content))
        for update in plan.updates:
            full_target = wiki_root / update.page
            tx.write_file(full_target, update.content)
            page_embeddings[update.page] = serialize(embedder.embed_text(update.content))
        _apply_pages_and_backrefs(tx=tx, plan=plan, embeddings=page_embeddings, source_id=source_row["id"])
        tx.write_file(wiki_root / "wiki" / "index.md", build_index(wiki_root))

    return "applied"


def _terminal_confirm_estimate(estimate: BatchCostEstimate) -> bool:
    """Default CLI prompt — print the estimate and ask for y/n."""
    print(
        f"\nBatch submission estimate:\n"
        f"  Sources: {estimate.requests}\n"
        f"  Input tokens: ~{estimate.input_tokens:,}\n"
        f"  Output tokens (max): ~{estimate.output_tokens_max:,}\n"
        f"  Estimated cost: ~${estimate.usd_total:.2f} (50% batch discount applied)\n"
        f"  Expected wall-clock: up to 60 minutes\n"
    )
    answer = input("Submit batch? [y/N] ").strip().lower()
    return answer in ("y", "yes")

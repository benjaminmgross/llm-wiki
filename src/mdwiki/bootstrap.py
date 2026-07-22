"""Provider-aware bootstrap path with native batch and same-provider sync fallback.

Providers that declare native batch capability use it. Other configured providers run
the same pending-source workflow synchronously and explicitly report the fallback. Each
source remains an isolated transaction; failures are persisted for status and retry.
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
    IngestError,
    _apply_pages_and_backrefs,
    _chunk_or_whole,
    _find_candidate_pages,
    _recent_log_entries,
    _reject_existing_new_page_paths,
    ingest_many,
)
from mdwiki.ingest_tool import INGEST_TOOL_CHOICE, INGEST_TOOL_DEFINITION
from mdwiki.llm import build_provider_from_config
from mdwiki.llm.base import (
    BatchCostEstimate,
    BatchRequest,
    Message,
    Provider,
)
from mdwiki.plan import PlanValidationError, allowed_kinds_for_wiki, parse_plan, parse_plan_dict
from mdwiki.prompts import INGEST_SYSTEM_PROMPT, INGEST_SYSTEM_PROMPT_TOOL_USE, build_ingest_user_prompt
from mdwiki.quote import min_quote_words_for_wiki, quote_normalize_mode_for_wiki, verify_plan
from mdwiki.source_state import mark_source_failed
from mdwiki.state import connect
from mdwiki.transaction import IngestTransaction


@dataclass(frozen=True)
class BootstrapFailure:
    """One source or provider-protocol failure from bootstrap ingest."""

    source_id: str
    original_path: str
    reason: str


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
        These are marked ``failed`` with durable reasons and remain retryable.
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
    mode: str = "native-batch"
    provider: str = ""
    failures: tuple[BootstrapFailure, ...] = ()


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
    on_fallback: Callable[[str], None] | None = None,
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
            mode="native-batch" if provider.supports_batch else "sync-fallback",
            provider=provider.name,
        )

    if not provider.supports_batch:
        if on_fallback is not None:
            on_fallback(provider.name)
        return _bootstrap_sync_fallback(
            wiki_root=wiki_root,
            provider=provider,
            embedder=embedder,
            pending=pending,
            on_progress=on_progress,
        )

    use_tool = provider.supports_tool_use

    requests: list[BatchRequest] = []
    contexts: dict[str, _RequestContext] = {}
    failures: list[BootstrapFailure] = []
    for source_row in pending:
        try:
            request, context = _prepare_request(
                wiki_root=wiki_root,
                source_row=source_row,
                embedder=embedder,
                use_tool=use_tool,
            )
        except Exception as exc:
            _record_failure(wiki_root, source_row=source_row, reason=f"request preparation failed: {exc}", failures=failures)
            continue
        requests.append(request)
        contexts[source_row["id"]] = context

    if not requests:
        return BootstrapResult(
            submitted=0,
            applied=0,
            failed=len(failures),
            skipped=0,
            cost_estimate=BatchCostEstimate(requests=0, input_tokens=0, output_tokens_max=0, usd_total=0.0),
            batch_id="",
            mode="native-batch",
            provider=provider.name,
            failures=tuple(failures),
        )

    estimate = provider.estimate_batch_cost(requests)
    if not yes:
        chooser = confirm or _terminal_confirm_estimate
        if not chooser(estimate):
            return BootstrapResult(
                submitted=0,
                applied=0,
                failed=len(failures),
                skipped=0,
                cost_estimate=estimate,
                batch_id="",
                mode="native-batch",
                provider=provider.name,
                failures=tuple(failures),
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
    skipped = 0
    batch_id = captured_batch_id
    grouped: dict[str, list[Any]] = {}
    for result in results:
        grouped.setdefault(result.custom_id, []).append(result)

    for custom_id in sorted(set(grouped) - set(contexts)):
        failures.append(
            BootstrapFailure(
                source_id=custom_id,
                original_path=f"<unknown:{custom_id}>",
                reason="provider returned a result for an unknown source id",
            )
        )

    for index, (source_id, context) in enumerate(contexts.items(), start=1):
        if on_progress is not None:
            on_progress(index, len(contexts), source_id)
        matches = grouped.get(source_id, [])
        if len(matches) != 1:
            reason = (
                "provider returned no result for submitted source"
                if not matches
                else f"provider returned {len(matches)} duplicate results for submitted source"
            )
            _record_failure(wiki_root, source_row=context.source_row, reason=reason, failures=failures)
            continue
        result = matches[0]
        if result.error is not None:
            _record_failure(wiki_root, source_row=context.source_row, reason=result.error, failures=failures)
            continue
        try:
            outcome, reason = _apply_one_result(
                wiki_root=wiki_root,
                embedder=embedder,
                response_text=result.text,
                context=context,
                tool_input=result.tool_input,
            )
        except Exception as exc:
            _record_failure(wiki_root, source_row=context.source_row, reason=str(exc), failures=failures)
            continue
        if outcome == "applied":
            applied += 1
        elif outcome == "skipped":
            skipped += 1
        else:
            _record_failure(wiki_root, source_row=context.source_row, reason=reason, failures=failures)

    return BootstrapResult(
        submitted=len(requests),
        applied=applied,
        failed=len(failures),
        skipped=skipped,
        cost_estimate=estimate,
        batch_id=batch_id,
        mode="native-batch",
        provider=provider.name,
        failures=tuple(failures),
    )


def _bootstrap_sync_fallback(
    *,
    wiki_root: Path,
    provider: Provider,
    embedder: Embedder,
    pending: list[dict[str, Any]],
    on_progress: Callable[[int, int, str], None] | None,
) -> BootstrapResult:
    by_id = {row["id"]: row["original_path"] for row in pending}
    results = ingest_many(
        wiki_root,
        scope="pending",
        yes=True,
        provider=provider,
        embedder=embedder,
        on_progress=on_progress,
    )
    failures = tuple(
        BootstrapFailure(
            source_id=result.source_id or "",
            original_path=by_id.get(result.source_id or "", "<unknown>"),
            reason=result.message.removeprefix("failed: "),
        )
        for result in results
        if not result.applied
    )
    skipped = sum(1 for result in results if result.applied and result.plan is not None and result.plan.is_empty())
    applied = sum(1 for result in results if result.applied) - skipped
    return BootstrapResult(
        submitted=len(results),
        applied=applied,
        failed=len(failures),
        skipped=skipped,
        cost_estimate=BatchCostEstimate(requests=0, input_tokens=0, output_tokens_max=0, usd_total=0.0),
        batch_id="",
        mode="sync-fallback",
        provider=provider.name,
        failures=failures,
    )


def _record_failure(
    wiki_root: Path,
    *,
    source_row: dict[str, Any],
    reason: str,
    failures: list[BootstrapFailure],
) -> None:
    source_id = source_row["id"]
    original_path = source_row["original_path"]
    mark_source_failed(wiki_root, source_id, reason)
    failures.append(BootstrapFailure(source_id=source_id, original_path=original_path, reason=reason))


def _load_pending(wiki_root: Path) -> list[dict[str, Any]]:
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, original_path, raw_path, status FROM sources "
            "WHERE status IN ('pending', 'failed') ORDER BY original_path"
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
) -> tuple[str, str]:
    """Parse, verify, and apply one batch result with a failure reason.

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
        _reject_existing_new_page_paths(wiki_root=wiki_root, plan=plan)
    except (PlanValidationError, IngestError) as exc:
        return "failed", f"invalid ingest plan: {exc}"

    verification = verify_plan(
        plan,
        source_text=context.source_text,
        section_ids=context.section_ids,
        mode=quote_normalize_mode_for_wiki(wiki_root),
        min_quote_words=min_quote_words_for_wiki(wiki_root),
    )
    if not verification.valid:
        details = "; ".join(error.message for error in verification.errors)
        return "failed", f"quote verification failed: {details}"

    source_row = context.source_row
    if plan.is_empty():
        with IngestTransaction(
            wiki_root=wiki_root,
            source_id=source_row["id"],
            summary=f"{plan.verdict}: {source_row['original_path']} — {plan.rationale}",
        ):
            pass
        return "skipped", ""

    summary = f"ingest {source_row['original_path']} → {len(plan.updates)} update(s), {len(plan.new_pages)} new page(s) [batch]"
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

    return "applied", ""


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

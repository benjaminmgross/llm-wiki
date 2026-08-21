"""Tests for ``mdwiki.bootstrap.bootstrap_batch`` — Anthropic Batch API path.

The Anthropic SDK is fully mocked; we never make real network calls in unit tests.
``provider.batch_complete`` and ``provider.estimate_batch_cost`` are mocked at the
class level so the bootstrap orchestrator's logic can be exercised end-to-end.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from mdwiki.bootstrap import BootstrapResult, bootstrap_batch
from mdwiki.cost_guard import CostBudgetExceededError
from mdwiki.init import init_wiki
from mdwiki.llm.base import (
    BatchCostEstimate,
    BatchRequest,
    BatchResult,
    CompleteResult,
    Message,
    PingResult,
    Provider,
)
from mdwiki.state import connect


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")


def _three_source_corpus(tmp_path: Path) -> Path:
    """Seed three .md files and run init so each is a pending source."""
    (tmp_path / "alpha.md").write_text("# Alpha\n\n## Intro\n\nAlpha discusses attention sinks for long contexts.\n")
    (tmp_path / "beta.md").write_text("# Beta\n\n## Intro\n\nBeta covers retrieval augmented generation patterns.\n")
    (tmp_path / "gamma.md").write_text("# Gamma\n\n## Intro\n\nGamma surveys quantization methods for inference.\n")
    init_wiki(tmp_path)
    return tmp_path


def _plan_for_source(slug: str, source_path: str, quote: str) -> str:
    return json.dumps(
        {
            "verdict": "ingest",
            "rationale": f"Adds {slug} concept.",
            "updates": [],
            "new_pages": [
                {
                    "path": f"wiki/concepts/{slug}.md",
                    "kind": "concept",
                    "content": f"# {slug.title()}\n\nA concept page from batch ingest.",
                    "claims": [{"source_section_id": f"{source_path}/Intro", "quote": quote}],
                }
            ],
            "cross_refs": [],
        }
    )


class StubEmbedder:
    def embed_text(self, _text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


class SyncOnlyProvider(Provider):
    name = "sync-only"

    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.complete_calls = 0

    def ping(self) -> PingResult:
        return PingResult(provider=self.name, model="test", latency_ms=0.0, ok=True, message="pong")

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        max_tokens: int = 1024,
        tools: list[dict[str, object]] | None = None,
        tool_choice: dict[str, object] | None = None,
    ) -> CompleteResult:
        self.complete_calls += 1
        return CompleteResult(text=next(self.responses), input_tokens=1, output_tokens=1)

    def estimate_batch_cost(self, requests: list[BatchRequest]) -> BatchCostEstimate:
        raise AssertionError("sync fallback must not estimate native batch cost")

    def batch_complete(self, requests: list[BatchRequest], **_kwargs: object) -> list[BatchResult]:
        raise AssertionError("sync fallback must not call native batch")


class NonAnthropicBatchProvider(SyncOnlyProvider):
    name = "other-batch"
    supports_batch = True

    def __init__(self, results: list[BatchResult]) -> None:
        super().__init__([])
        self.results = results
        self.requests: list[BatchRequest] = []

    def estimate_batch_cost(self, requests: list[BatchRequest]) -> BatchCostEstimate:
        return BatchCostEstimate(requests=len(requests), input_tokens=1, output_tokens_max=1, usd_total=0.01)

    def batch_complete(self, requests: list[BatchRequest], **_kwargs: object) -> list[BatchResult]:
        self.requests = requests
        return self.results


@pytest.mark.unit
def test_bootstrap_batch_explicitly_falls_back_to_same_non_batch_provider(tmp_path: Path) -> None:
    wiki = _three_source_corpus(tmp_path)
    provider = SyncOnlyProvider(
        [
            _plan_for_source("alpha-sync", "alpha.md", "alpha discusses attention sinks for long contexts"),
            _plan_for_source("beta-sync", "beta.md", "beta covers retrieval augmented generation patterns"),
            _plan_for_source("gamma-sync", "gamma.md", "gamma surveys quantization methods for inference"),
        ]
    )
    fallbacks: list[str] = []

    result = bootstrap_batch(
        wiki,
        yes=True,
        provider=provider,
        embedder=StubEmbedder(),  # type: ignore[arg-type]
        on_fallback=fallbacks.append,
    )

    assert result.mode == "sync-fallback"
    assert result.provider == "sync-only"
    assert result.applied == 3
    assert result.failed == 0
    assert provider.complete_calls == 3
    assert fallbacks == ["sync-only"]


@pytest.mark.unit
def test_sync_fallback_isolates_source_preparation_failure(tmp_path: Path) -> None:
    wiki = _three_source_corpus(tmp_path)

    class FailingEmbedder(StubEmbedder):
        def embed_text(self, text: str) -> list[float]:
            if "retrieval augmented" in text:
                raise ValueError("unsupported beta content")
            return super().embed_text(text)

    provider = SyncOnlyProvider(
        [
            _plan_for_source("alpha-sync-isolated", "alpha.md", "alpha discusses attention sinks for long contexts"),
            _plan_for_source("gamma-sync-isolated", "gamma.md", "gamma surveys quantization methods for inference"),
        ]
    )

    result = bootstrap_batch(
        wiki,
        yes=True,
        provider=provider,
        embedder=FailingEmbedder(),  # type: ignore[arg-type]
    )

    assert result.mode == "sync-fallback"
    assert result.applied == 2
    assert result.failed == 1
    assert provider.complete_calls == 2
    assert result.failures[0].original_path == "beta.md"
    assert "unsupported beta content" in result.failures[0].reason
    with connect(wiki / ".mdwiki" / "state.db") as conn:
        statuses = {
            row["original_path"]: row["status"]
            for row in conn.execute("SELECT original_path, status FROM sources")
        }
    assert statuses == {"alpha.md": "ingested", "beta.md": "failed", "gamma.md": "ingested"}


@pytest.mark.unit
def test_sync_fallback_rolls_back_page_embedding_failure_and_continues(tmp_path: Path) -> None:
    wiki = _three_source_corpus(tmp_path)

    class FailingPageEmbedder(StubEmbedder):
        def embed_text(self, text: str) -> list[float]:
            if "beta-apply-failure" in text.lower():
                raise ValueError("cannot embed generated beta page")
            return super().embed_text(text)

    provider = SyncOnlyProvider(
        [
            _plan_for_source("alpha-apply-ok", "alpha.md", "alpha discusses attention sinks for long contexts"),
            _plan_for_source("beta-apply-failure", "beta.md", "beta covers retrieval augmented generation patterns"),
            _plan_for_source("gamma-apply-ok", "gamma.md", "gamma surveys quantization methods for inference"),
        ]
    )

    result = bootstrap_batch(
        wiki,
        yes=True,
        provider=provider,
        embedder=FailingPageEmbedder(),  # type: ignore[arg-type]
    )

    assert result.applied == 2
    assert result.failed == 1
    assert provider.complete_calls == 3
    assert result.failures[0].original_path == "beta.md"
    assert "cannot embed generated beta page" in result.failures[0].reason
    assert not (wiki / "wiki" / "concepts" / "beta-apply-failure.md").exists()
    assert (wiki / "wiki" / "concepts" / "gamma-apply-ok.md").exists()
    with connect(wiki / ".mdwiki" / "state.db") as conn:
        beta = conn.execute("SELECT status, failure_reason FROM sources WHERE original_path = 'beta.md'").fetchone()
        beta_pages = conn.execute("SELECT COUNT(*) AS c FROM pages WHERE path LIKE '%beta-apply-failure%'").fetchone()["c"]
    assert beta["status"] == "failed"
    assert "cannot embed generated beta page" in beta["failure_reason"]
    assert beta_pages == 0


@pytest.mark.unit
def test_non_anthropic_provider_with_batch_capability_uses_native_batch(tmp_path: Path) -> None:
    wiki = _three_source_corpus(tmp_path)
    with connect(wiki / ".mdwiki" / "state.db") as conn:
        rows = conn.execute("SELECT id, original_path FROM sources ORDER BY original_path").fetchall()
    by_path = {row["original_path"]: row["id"] for row in rows}
    provider = NonAnthropicBatchProvider(
        [
            BatchResult(
                custom_id=by_path["alpha.md"],
                text=_plan_for_source("alpha-other", "alpha.md", "alpha discusses attention sinks for long contexts"),
            ),
            BatchResult(
                custom_id=by_path["beta.md"],
                text=_plan_for_source("beta-other", "beta.md", "beta covers retrieval augmented generation patterns"),
            ),
            BatchResult(
                custom_id=by_path["gamma.md"],
                text=_plan_for_source("gamma-other", "gamma.md", "gamma surveys quantization methods for inference"),
            ),
        ]
    )

    result = bootstrap_batch(
        wiki,
        yes=True,
        provider=provider,
        embedder=StubEmbedder(),  # type: ignore[arg-type]
        poll_interval=0.0,
    )

    assert result.mode == "native-batch"
    assert result.provider == "other-batch"
    assert result.applied == 3
    assert provider.complete_calls == 0
    assert len(provider.requests) == 3
    assert all(request.tools is None for request in provider.requests)


@pytest.mark.unit
def test_bootstrap_batch_submits_one_batch_per_pending_source(tmp_path: Path, mocker: MockerFixture) -> None:
    """Three pending sources → one ``batch_complete`` call with three requests."""
    wiki = _three_source_corpus(tmp_path)
    with connect(wiki / ".mdwiki" / "state.db") as conn:
        rows = conn.execute("SELECT id, original_path FROM sources ORDER BY original_path").fetchall()
    by_path = {r["original_path"]: r["id"] for r in rows}

    fake_estimate = BatchCostEstimate(requests=3, input_tokens=3000, output_tokens_max=48000, usd_total=0.36)
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.estimate_batch_cost",
        return_value=fake_estimate,
    )
    mock_batch = mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.batch_complete",
        return_value=[
            BatchResult(
                custom_id=by_path["alpha.md"],
                text=_plan_for_source("alpha-concept", "alpha.md", "alpha discusses attention sinks for long contexts"),
            ),
            BatchResult(
                custom_id=by_path["beta.md"],
                text=_plan_for_source("beta-concept", "beta.md", "beta covers retrieval augmented generation patterns"),
            ),
            BatchResult(
                custom_id=by_path["gamma.md"],
                text=_plan_for_source("gamma-concept", "gamma.md", "gamma surveys quantization methods for inference"),
            ),
        ],
    )

    result = bootstrap_batch(wiki, yes=True, poll_interval=0.0)

    mock_batch.assert_called_once()
    submitted_requests = mock_batch.call_args.args[0]
    assert len(submitted_requests) == 3
    assert {r.custom_id for r in submitted_requests} == set(by_path.values())

    assert isinstance(result, BootstrapResult)
    assert result.submitted == 3
    assert result.applied == 3
    assert result.failed == 0


@pytest.mark.unit
def test_bootstrap_batch_blocks_when_estimate_exceeds_daily_budget(tmp_path: Path) -> None:
    wiki = _three_source_corpus(tmp_path)
    config_path = wiki / ".mdwiki" / "config.toml"
    config_path.write_text(config_path.read_text() + "\n[cost_guard]\ndaily_budget_usd = 0.005\n")
    provider = NonAnthropicBatchProvider([])

    with pytest.raises(CostBudgetExceededError, match="projected"):
        bootstrap_batch(
            wiki,
            yes=True,
            provider=provider,
            embedder=StubEmbedder(),  # type: ignore[arg-type]
            poll_interval=0.0,
        )

    assert provider.requests == []
    with connect(wiki / ".mdwiki" / "state.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM cost_ledger").fetchone()[0] == 0


@pytest.mark.unit
def test_bootstrap_batch_override_records_estimated_cost_receipt(tmp_path: Path) -> None:
    wiki = _three_source_corpus(tmp_path)
    config_path = wiki / ".mdwiki" / "config.toml"
    config_path.write_text(config_path.read_text() + "\n[cost_guard]\ndaily_budget_usd = 0.005\n")
    with connect(wiki / ".mdwiki" / "state.db") as conn:
        by_path = {
            row["original_path"]: row["id"]
            for row in conn.execute("SELECT id, original_path FROM sources ORDER BY original_path")
        }
    provider = NonAnthropicBatchProvider(
        [
            BatchResult(
                custom_id=by_path["alpha.md"],
                text=_plan_for_source("alpha-receipt", "alpha.md", "alpha discusses attention sinks for long contexts"),
                input_tokens=100,
                output_tokens=20,
            ),
            BatchResult(
                custom_id=by_path["beta.md"],
                text=_plan_for_source("beta-receipt", "beta.md", "beta covers retrieval augmented generation patterns"),
                input_tokens=110,
                output_tokens=25,
            ),
            BatchResult(
                custom_id=by_path["gamma.md"],
                text=_plan_for_source("gamma-receipt", "gamma.md", "gamma surveys quantization methods for inference"),
                input_tokens=120,
                output_tokens=30,
            ),
        ]
    )

    result = bootstrap_batch(
        wiki,
        yes=True,
        cost_guard_override=True,
        provider=provider,
        embedder=StubEmbedder(),  # type: ignore[arg-type]
        poll_interval=0.0,
    )

    assert result.cost_estimate.usd_total == pytest.approx(0.01)
    with connect(wiki / ".mdwiki" / "state.db") as conn:
        receipt = conn.execute(
            "SELECT operation, tokens_in, tokens_out, cost_usd FROM cost_ledger"
        ).fetchone()
    assert tuple(receipt) == ("bootstrap_batch_estimate", 330, 75, pytest.approx(0.01))


@pytest.mark.unit
def test_bootstrap_batch_failed_results_persist_reasons_for_retry(tmp_path: Path, mocker: MockerFixture) -> None:
    """Errored batch results become failed with durable reasons and remain retryable."""
    wiki = _three_source_corpus(tmp_path)
    with connect(wiki / ".mdwiki" / "state.db") as conn:
        rows = conn.execute("SELECT id, original_path FROM sources ORDER BY original_path").fetchall()
    by_path = {r["original_path"]: r["id"] for r in rows}

    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.estimate_batch_cost",
        return_value=BatchCostEstimate(requests=3, input_tokens=1, output_tokens_max=1, usd_total=0.01),
    )
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.batch_complete",
        return_value=[
            BatchResult(
                custom_id=by_path["alpha.md"],
                text=_plan_for_source("alpha-concept", "alpha.md", "alpha discusses attention sinks for long contexts"),
            ),
            BatchResult(custom_id=by_path["beta.md"], text="", error="rate_limited"),
            BatchResult(custom_id=by_path["gamma.md"], text="", error="invalid_request"),
        ],
    )

    result = bootstrap_batch(wiki, yes=True, poll_interval=0.0)
    assert result.applied == 1
    assert result.failed == 2

    with connect(wiki / ".mdwiki" / "state.db") as conn:
        statuses = {
            r["original_path"]: (r["status"], r["failure_reason"])
            for r in conn.execute("SELECT original_path, status, failure_reason FROM sources")
        }
    assert statuses["alpha.md"] == ("ingested", None)
    assert statuses["beta.md"] == ("failed", "rate_limited")
    assert statuses["gamma.md"] == ("failed", "invalid_request")
    assert [failure.original_path for failure in result.failures] == ["beta.md", "gamma.md"]


@pytest.mark.unit
def test_native_batch_rerun_submits_only_failed_sources(tmp_path: Path) -> None:
    wiki = _three_source_corpus(tmp_path)
    with connect(wiki / ".mdwiki" / "state.db") as conn:
        rows = conn.execute("SELECT id, original_path FROM sources ORDER BY original_path").fetchall()
    by_path = {row["original_path"]: row["id"] for row in rows}
    first_provider = NonAnthropicBatchProvider(
        [
            BatchResult(
                custom_id=by_path["alpha.md"],
                text=_plan_for_source("alpha-first", "alpha.md", "alpha discusses attention sinks for long contexts"),
            ),
            BatchResult(custom_id=by_path["beta.md"], text="", error="temporary beta failure"),
            BatchResult(
                custom_id=by_path["gamma.md"],
                text=_plan_for_source("gamma-first", "gamma.md", "gamma surveys quantization methods for inference"),
            ),
        ]
    )
    first = bootstrap_batch(wiki, yes=True, provider=first_provider, embedder=StubEmbedder(), poll_interval=0.0)  # type: ignore[arg-type]
    assert first.failed == 1

    retry_provider = NonAnthropicBatchProvider(
        [
            BatchResult(
                custom_id=by_path["beta.md"],
                text=_plan_for_source("beta-retry", "beta.md", "beta covers retrieval augmented generation patterns"),
            )
        ]
    )
    retry = bootstrap_batch(wiki, yes=True, provider=retry_provider, embedder=StubEmbedder(), poll_interval=0.0)  # type: ignore[arg-type]

    assert [request.custom_id for request in retry_provider.requests] == [by_path["beta.md"]]
    assert retry.applied == 1
    assert retry.failed == 0
    with connect(wiki / ".mdwiki" / "state.db") as conn:
        remaining = conn.execute("SELECT COUNT(*) AS c FROM sources WHERE status != 'ingested'").fetchone()["c"]
    assert remaining == 0


@pytest.mark.unit
def test_bootstrap_batch_marks_missing_provider_result_failed(tmp_path: Path, mocker: MockerFixture) -> None:
    wiki = _three_source_corpus(tmp_path)
    with connect(wiki / ".mdwiki" / "state.db") as conn:
        rows = conn.execute("SELECT id, original_path FROM sources ORDER BY original_path").fetchall()
    by_path = {row["original_path"]: row["id"] for row in rows}
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.estimate_batch_cost",
        return_value=BatchCostEstimate(requests=3, input_tokens=1, output_tokens_max=1, usd_total=0.01),
    )
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.batch_complete",
        return_value=[
            BatchResult(
                custom_id=by_path["alpha.md"],
                text=_plan_for_source("alpha-present", "alpha.md", "alpha discusses attention sinks for long contexts"),
            ),
            BatchResult(
                custom_id=by_path["beta.md"],
                text=_plan_for_source("beta-present", "beta.md", "beta covers retrieval augmented generation patterns"),
            ),
        ],
    )

    result = bootstrap_batch(wiki, yes=True, embedder=StubEmbedder(), poll_interval=0.0)  # type: ignore[arg-type]

    assert result.failed == 1
    assert result.failures[0].original_path == "gamma.md"
    assert "no result" in result.failures[0].reason.lower()
    with connect(wiki / ".mdwiki" / "state.db") as conn:
        gamma = conn.execute("SELECT status, failure_reason FROM sources WHERE original_path = 'gamma.md'").fetchone()
    assert gamma["status"] == "failed"
    assert "no result" in gamma["failure_reason"].lower()


@pytest.mark.unit
def test_bootstrap_batch_isolates_request_preparation_failure(tmp_path: Path) -> None:
    wiki = _three_source_corpus(tmp_path)
    with connect(wiki / ".mdwiki" / "state.db") as conn:
        rows = conn.execute("SELECT id, original_path FROM sources ORDER BY original_path").fetchall()
    by_path = {row["original_path"]: row["id"] for row in rows}

    class FailingEmbedder(StubEmbedder):
        def embed_text(self, text: str) -> list[float]:
            if "retrieval augmented" in text:
                raise ValueError("unsupported beta content")
            return super().embed_text(text)

    provider = NonAnthropicBatchProvider(
        [
            BatchResult(
                custom_id=by_path["alpha.md"],
                text=_plan_for_source("alpha-prepared", "alpha.md", "alpha discusses attention sinks for long contexts"),
            ),
            BatchResult(
                custom_id=by_path["gamma.md"],
                text=_plan_for_source("gamma-prepared", "gamma.md", "gamma surveys quantization methods for inference"),
            ),
        ]
    )

    result = bootstrap_batch(wiki, yes=True, provider=provider, embedder=FailingEmbedder(), poll_interval=0.0)  # type: ignore[arg-type]

    assert {request.custom_id for request in provider.requests} == {by_path["alpha.md"], by_path["gamma.md"]}
    assert result.applied == 2
    assert result.failed == 1
    assert result.failures[0].original_path == "beta.md"
    assert "unsupported beta content" in result.failures[0].reason
    with connect(wiki / ".mdwiki" / "state.db") as conn:
        statuses = {
            row["original_path"]: row["status"]
            for row in conn.execute("SELECT original_path, status FROM sources")
        }
    assert statuses == {"alpha.md": "ingested", "beta.md": "failed", "gamma.md": "ingested"}


@pytest.mark.unit
def test_bootstrap_batch_keeps_commit_when_sidecar_mirror_is_corrupt(tmp_path: Path) -> None:
    wiki = _three_source_corpus(tmp_path)
    with connect(wiki / ".mdwiki" / "state.db") as conn:
        rows = conn.execute("SELECT id, original_path FROM sources ORDER BY original_path").fetchall()
    by_path = {row["original_path"]: row["id"] for row in rows}
    provider = NonAnthropicBatchProvider(
        [
            BatchResult(
                custom_id=by_path[path],
                text=_plan_for_source(f"{path[:-3]}-committed", path, quote),
            )
            for path, quote in (
                ("alpha.md", "alpha discusses attention sinks for long contexts"),
                ("beta.md", "beta covers retrieval augmented generation patterns"),
                ("gamma.md", "gamma surveys quantization methods for inference"),
            )
        ]
    )
    (wiki / "raw" / ".sources.json").write_text("{corrupt")

    result = bootstrap_batch(wiki, yes=True, provider=provider, embedder=StubEmbedder(), poll_interval=0.0)  # type: ignore[arg-type]

    assert result.applied == 3
    assert result.failed == 0
    with connect(wiki / ".mdwiki" / "state.db") as conn:
        statuses = [row["status"] for row in conn.execute("SELECT status FROM sources")]
    assert statuses == ["ingested", "ingested", "ingested"]


@pytest.mark.unit
def test_bootstrap_batch_reports_duplicate_and_unknown_provider_results(tmp_path: Path) -> None:
    wiki = _three_source_corpus(tmp_path)
    with connect(wiki / ".mdwiki" / "state.db") as conn:
        rows = conn.execute("SELECT id, original_path FROM sources ORDER BY original_path").fetchall()
    by_path = {row["original_path"]: row["id"] for row in rows}
    alpha = BatchResult(
        custom_id=by_path["alpha.md"],
        text=_plan_for_source("alpha-duplicate", "alpha.md", "alpha discusses attention sinks for long contexts"),
    )
    provider = NonAnthropicBatchProvider(
        [
            alpha,
            alpha,
            BatchResult(
                custom_id=by_path["beta.md"],
                text=_plan_for_source("beta-present", "beta.md", "beta covers retrieval augmented generation patterns"),
            ),
            BatchResult(
                custom_id=by_path["gamma.md"],
                text=_plan_for_source("gamma-present", "gamma.md", "gamma surveys quantization methods for inference"),
            ),
            BatchResult(custom_id="unknown-id", text="{}"),
        ]
    )

    result = bootstrap_batch(wiki, yes=True, provider=provider, embedder=StubEmbedder(), poll_interval=0.0)  # type: ignore[arg-type]

    assert result.applied == 2
    assert result.failed == 2
    assert any("duplicate" in failure.reason for failure in result.failures)
    assert any(failure.source_id == "unknown-id" for failure in result.failures)


@pytest.mark.unit
def test_bootstrap_batch_aborts_when_user_rejects_estimate(tmp_path: Path, mocker: MockerFixture) -> None:
    """``confirm`` returning False → no submission; result.submitted == 0."""
    wiki = _three_source_corpus(tmp_path)

    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.estimate_batch_cost",
        return_value=BatchCostEstimate(requests=3, input_tokens=1, output_tokens_max=1, usd_total=999.99),
    )
    mock_batch = mocker.patch("mdwiki.llm.anthropic.AnthropicProvider.batch_complete")

    result = bootstrap_batch(wiki, yes=False, confirm=lambda _est: False, poll_interval=0.0)
    assert result.submitted == 0
    assert result.applied == 0
    mock_batch.assert_not_called()


@pytest.mark.unit
def test_declined_batch_preserves_preparation_failures(tmp_path: Path) -> None:
    wiki = _three_source_corpus(tmp_path)

    class FailingEmbedder(StubEmbedder):
        def embed_text(self, text: str) -> list[float]:
            if "retrieval augmented" in text:
                raise ValueError("unsupported beta content")
            return super().embed_text(text)

    provider = NonAnthropicBatchProvider([])

    result = bootstrap_batch(
        wiki,
        yes=False,
        confirm=lambda _estimate: False,
        provider=provider,
        embedder=FailingEmbedder(),  # type: ignore[arg-type]
        poll_interval=0.0,
    )

    assert result.submitted == 0
    assert result.failed == 1
    assert result.failures[0].original_path == "beta.md"
    assert provider.requests == []
    with connect(wiki / ".mdwiki" / "state.db") as conn:
        beta = conn.execute("SELECT status, failure_reason FROM sources WHERE original_path = 'beta.md'").fetchone()
    assert beta["status"] == "failed"
    assert "unsupported beta content" in beta["failure_reason"]


@pytest.mark.unit
def test_bootstrap_batch_no_pending_returns_zero_result(tmp_path: Path, mocker: MockerFixture) -> None:
    """Empty corpus → no batch call, zero counts everywhere."""
    init_wiki(tmp_path)
    mock_batch = mocker.patch("mdwiki.llm.anthropic.AnthropicProvider.batch_complete")

    result = bootstrap_batch(tmp_path, yes=True, poll_interval=0.0)
    assert result.submitted == 0
    mock_batch.assert_not_called()


@pytest.mark.unit
def test_bootstrap_batch_unparseable_response_counted_as_failed(tmp_path: Path, mocker: MockerFixture) -> None:
    """A successful batch entry with bad JSON becomes failed with a retryable reason."""
    wiki = _three_source_corpus(tmp_path)
    with connect(wiki / ".mdwiki" / "state.db") as conn:
        rows = conn.execute("SELECT id, original_path FROM sources ORDER BY original_path").fetchall()
    by_path = {r["original_path"]: r["id"] for r in rows}

    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.estimate_batch_cost",
        return_value=BatchCostEstimate(requests=3, input_tokens=1, output_tokens_max=1, usd_total=0.01),
    )
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.batch_complete",
        return_value=[
            BatchResult(custom_id=by_path["alpha.md"], text="not valid json"),
            BatchResult(
                custom_id=by_path["beta.md"],
                text=_plan_for_source("beta-concept", "beta.md", "beta covers retrieval augmented generation patterns"),
            ),
            BatchResult(custom_id=by_path["gamma.md"], text='{"verdict": "skip"}'),
        ],
    )

    result = bootstrap_batch(wiki, yes=True, poll_interval=0.0)
    # alpha = unparseable JSON (failed), beta = applied, gamma = invalid plan structure (failed)
    assert result.applied == 1
    assert result.failed == 2


@pytest.mark.unit
def test_bootstrap_batch_accepts_profile_specific_page_kinds(tmp_path: Path, mocker: MockerFixture) -> None:
    """A ``framework``-profile wiki must accept ``procedure`` page kinds in batch results.

    Regression: previously ``_apply_one_result`` called ``parse_plan(response_text)``
    without ``allowed_kinds``, so every batch result that proposed a profile-
    specific kind (``procedure``, ``meeting``, ``workstream``, etc.) was
    rejected as "Invalid page kind" and counted as failed. The fix mirrors the
    sync path by passing ``allowed_kinds_for_wiki(wiki_root)``.
    """
    (tmp_path / "framework-doc.md").write_text("# Framework Doc\n\n## Intro\n\nDescribes a checklist procedure for code review.\n")
    init_wiki(tmp_path, profile="framework")
    with connect(tmp_path / ".mdwiki" / "state.db") as conn:
        rows = conn.execute("SELECT id, original_path FROM sources").fetchall()
    by_path = {r["original_path"]: r["id"] for r in rows}

    procedure_plan = json.dumps(
        {
            "verdict": "ingest",
            "rationale": "Captures the review checklist as a procedure.",
            "updates": [],
            "new_pages": [
                {
                    "path": "wiki/procedures/code-review-checklist.md",
                    "kind": "procedure",
                    "content": "# Code Review Checklist\n\nA standard procedure.",
                    "claims": [
                        {
                            "source_section_id": "framework-doc.md/Intro",
                            "quote": "describes a checklist procedure for code review",
                        }
                    ],
                }
            ],
            "cross_refs": [],
        }
    )

    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.estimate_batch_cost",
        return_value=BatchCostEstimate(requests=1, input_tokens=1, output_tokens_max=1, usd_total=0.01),
    )
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.batch_complete",
        return_value=[BatchResult(custom_id=by_path["framework-doc.md"], text=procedure_plan)],
    )

    result = bootstrap_batch(tmp_path, yes=True, poll_interval=0.0)
    assert result.applied == 1
    assert result.failed == 0


@pytest.mark.unit
def test_cli_init_bootstrap_batch_handles_batch_timeout_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mocker: MockerFixture,
) -> None:
    """``mdwiki init --bootstrap-batch`` surfaces ``BatchTimeoutError`` as a friendly error.

    Without the CLI catch, a 24h batch poll timeout would dump a RuntimeError
    stack trace; the CLI must intercept it and print ``error: ...`` to stderr.
    """
    from mdwiki.cli import main
    from mdwiki.llm.anthropic import BatchTimeoutError

    (tmp_path / "a.md").write_text("# A\n\n## intro\n\nbody.\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.estimate_batch_cost",
        return_value=BatchCostEstimate(requests=1, input_tokens=10, output_tokens_max=100, usd_total=0.01),
    )
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.batch_complete",
        side_effect=BatchTimeoutError("Batch batch_xyz did not finish within 24h (last status: 'in_progress')."),
    )

    exit_code = main(["init", "--bootstrap-batch", "--yes"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Batch batch_xyz" in captured.err
    assert "error:" in captured.err


@pytest.mark.unit
def test_cli_init_bootstrap_batch_surfaces_provider_config_value_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mocker: MockerFixture,
) -> None:
    """A ValueError from build_provider_from_config (e.g. missing base_url) → friendly stderr."""
    from mdwiki.cli import main

    (tmp_path / "a.md").write_text("# A\n\n## intro\n\nbody.\n")
    monkeypatch.chdir(tmp_path)
    mocker.patch(
        "mdwiki.bootstrap.build_provider_from_config",
        side_effect=ValueError("provider 'openai-compatible' requires [llm.openai_compatible].base_url"),
    )

    exit_code = main(["init", "--bootstrap-batch", "--yes"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "provider config invalid" in captured.err
    assert "base_url" in captured.err


@pytest.mark.unit
def test_cli_init_bootstrap_batch_handles_unexpected_status_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mocker: MockerFixture,
) -> None:
    """``mdwiki init --bootstrap-batch`` surfaces ``BatchUnexpectedStatusError`` as a friendly error."""
    from mdwiki.cli import main
    from mdwiki.llm.anthropic import BatchUnexpectedStatusError

    (tmp_path / "a.md").write_text("# A\n\n## intro\n\nbody.\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.estimate_batch_cost",
        return_value=BatchCostEstimate(requests=1, input_tokens=10, output_tokens_max=100, usd_total=0.01),
    )
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.batch_complete",
        side_effect=BatchUnexpectedStatusError("Batch batch_abc entered status 'expired' — it will not produce applicable results."),
    )

    exit_code = main(["init", "--bootstrap-batch", "--yes"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "expired" in captured.err
    assert "error:" in captured.err

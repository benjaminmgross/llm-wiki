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
from mdwiki.init import init_wiki
from mdwiki.llm.base import BatchCostEstimate, BatchResult
from mdwiki.state import connect


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")


def _three_source_corpus(tmp_path: Path) -> Path:
    """Seed three .md files and run init so each is a pending source."""
    (tmp_path / "alpha.md").write_text(
        "# Alpha\n\n## Intro\n\nAlpha discusses attention sinks for long contexts.\n"
    )
    (tmp_path / "beta.md").write_text(
        "# Beta\n\n## Intro\n\nBeta covers retrieval augmented generation patterns.\n"
    )
    (tmp_path / "gamma.md").write_text(
        "# Gamma\n\n## Intro\n\nGamma surveys quantization methods for inference.\n"
    )
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
def test_bootstrap_batch_failed_results_leave_sources_pending(tmp_path: Path, mocker: MockerFixture) -> None:
    """Errored batch results don't apply; sources stay pending in DB; counter increments."""
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
        statuses = {r["original_path"]: r["status"] for r in conn.execute("SELECT original_path, status FROM sources")}
    assert statuses["alpha.md"] == "ingested"
    assert statuses["beta.md"] == "pending"
    assert statuses["gamma.md"] == "pending"


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
def test_bootstrap_batch_no_pending_returns_zero_result(tmp_path: Path, mocker: MockerFixture) -> None:
    """Empty corpus → no batch call, zero counts everywhere."""
    init_wiki(tmp_path)
    mock_batch = mocker.patch("mdwiki.llm.anthropic.AnthropicProvider.batch_complete")

    result = bootstrap_batch(tmp_path, yes=True, poll_interval=0.0)
    assert result.submitted == 0
    mock_batch.assert_not_called()


@pytest.mark.unit
def test_bootstrap_batch_unparseable_response_counted_as_failed(tmp_path: Path, mocker: MockerFixture) -> None:
    """A successful batch entry with bad JSON → counted as failed; source stays pending."""
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
        side_effect=BatchTimeoutError(
            "Batch batch_xyz did not finish within 24h (last status: 'in_progress')."
        ),
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
        side_effect=ValueError(
            "provider 'openai-compatible' requires [llm.openai_compatible].base_url"
        ),
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
        side_effect=BatchUnexpectedStatusError(
            "Batch batch_abc entered status 'expired' — it will not produce applicable results."
        ),
    )

    exit_code = main(["init", "--bootstrap-batch", "--yes"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "expired" in captured.err
    assert "error:" in captured.err

"""Tests for ``mdwiki.doctor`` — pre-flight check for an initialized wiki."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from mdwiki.doctor import DoctorReport, format_report, run_doctor
from mdwiki.init import init_wiki
from mdwiki.llm.base import PingResult


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")


@pytest.mark.unit
def test_run_doctor_returns_green_on_successful_ping(tmp_path: Path, mocker: MockerFixture) -> None:
    init_wiki(tmp_path)
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.ping",
        return_value=PingResult(provider="anthropic", model="claude-sonnet-4-6", latency_ms=120.0, ok=True, message="pong"),
    )
    report = run_doctor(tmp_path)
    assert report.api_ok is True
    assert report.provider == "anthropic"
    assert report.model == "claude-sonnet-4-6"
    assert report.latency_ms == 120.0


@pytest.mark.unit
def test_run_doctor_returns_red_on_auth_failure(tmp_path: Path, mocker: MockerFixture) -> None:
    init_wiki(tmp_path)
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.ping",
        return_value=PingResult(provider="anthropic", model="claude-sonnet-4-6", latency_ms=10.0, ok=False, message="auth failed"),
    )
    report = run_doctor(tmp_path)
    assert report.api_ok is False
    assert "auth" in report.api_message.lower()


@pytest.mark.unit
def test_run_doctor_reports_embedder_name(tmp_path: Path, mocker: MockerFixture) -> None:
    init_wiki(tmp_path)
    mocker.patch(
        "mdwiki.llm.anthropic.AnthropicProvider.ping",
        return_value=PingResult(provider="anthropic", model="claude-sonnet-4-6", latency_ms=1.0, ok=True, message="ok"),
    )
    report = run_doctor(tmp_path)
    assert "all-MiniLM-L6-v2" in report.embedder_model


@pytest.mark.unit
def test_format_report_renders_status_lines() -> None:
    report = DoctorReport(
        wiki_root=Path("/tmp/wiki"),
        provider="anthropic",
        model="claude-sonnet-4-6",
        api_ok=True,
        latency_ms=42.5,
        api_message="pong",
        embedder_model="sentence-transformers/all-MiniLM-L6-v2",
    )
    out = format_report(report)
    assert "anthropic" in out
    assert "claude-sonnet-4-6" in out
    assert "42" in out  # latency
    assert "all-MiniLM-L6-v2" in out
    assert "ok" in out.lower()


@pytest.mark.unit
def test_format_report_marks_red_on_failure() -> None:
    report = DoctorReport(
        wiki_root=Path("/tmp/wiki"),
        provider="anthropic",
        model="claude-fake",
        api_ok=False,
        latency_ms=10.0,
        api_message="model not found",
        embedder_model="sentence-transformers/all-MiniLM-L6-v2",
    )
    out = format_report(report)
    assert "fail" in out.lower() or "error" in out.lower() or "not found" in out.lower()

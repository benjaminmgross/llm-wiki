"""Pre-flight check for an initialized wiki — ``mdwiki doctor``.

Reads the LLM config, makes a single 1-token API call, and reports whether the
provider, model, and embedder are reachable. The embedder model is reported by
name only — no model load — because lazy-loading it during doctor would slow the
cheap "is this set up correctly?" question to several seconds.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mdwiki.config import load_config
from mdwiki.llm import build_provider_from_config

DEFAULT_EMBEDDER_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"


@dataclass(frozen=True)
class DoctorReport:
    """Structured result of a doctor run."""

    wiki_root: Path
    provider: str
    model: str
    api_ok: bool
    latency_ms: float
    api_message: str
    embedder_model: str


def run_doctor(wiki_root: Path) -> DoctorReport:
    """Build the configured provider, ping it, and return the report.

    Parameters
    ----------
    wiki_root : Path
        Directory containing ``.mdwiki/``.
    """
    provider = build_provider_from_config(wiki_root)
    ping = provider.ping()
    config = load_config(wiki_root)
    embedder_model = config.get("embedder", {}).get("model", DEFAULT_EMBEDDER_MODEL)
    return DoctorReport(
        wiki_root=wiki_root,
        provider=provider.name,
        model=provider.model,
        api_ok=ping.ok,
        latency_ms=ping.latency_ms,
        api_message=ping.message,
        embedder_model=embedder_model,
    )


def format_report(report: DoctorReport) -> str:
    """Render the report as a single multi-line block for the CLI."""
    api_status = "ok" if report.api_ok else "FAIL"
    api_line = f"api ping: {api_status} ({report.latency_ms:.0f} ms) — {report.api_message}"
    lines = [
        f"mdwiki at {report.wiki_root}",
        "",
        f"provider: {report.provider}",
        f"model:    {report.model}",
        f"{api_line}",
        f"embedder: {report.embedder_model} (local)",
    ]
    return "\n".join(lines)

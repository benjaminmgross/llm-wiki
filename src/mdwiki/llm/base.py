"""Provider abstract base class — the seam that lets v1.0.0 swap to local models on Day 2.

A ``Provider`` wraps a single LLM backend (Anthropic in v1.0.0; Qwen, Kimi, etc. in
later releases). Every provider exposes the same surface: ``ping`` for health checks,
``complete`` for one-shot inference. Batch and cost estimation arrive in Phase 7
when ``init --bootstrap`` actually needs them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Message:
    """One turn in a chat-style exchange.

    Parameters
    ----------
    role : "user" | "assistant"
        Who produced the content.
    content : str
        The textual content of the turn.
    """

    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class CompleteResult:
    """The outcome of one ``complete()`` call — surface metrics needed for cost tracking."""

    text: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


@dataclass(frozen=True)
class PingResult:
    """Outcome of ``ping()`` — used by ``mdwiki doctor`` to format a single-line status."""

    provider: str
    model: str
    latency_ms: float
    ok: bool
    message: str


class Provider(ABC):
    """Abstract base for an LLM provider.

    Subclasses must implement ``ping`` and ``complete``. Concrete providers also set
    a class-level ``name`` (e.g. ``"anthropic"``) used by the factory for routing.
    """

    name: str = ""

    @abstractmethod
    def ping(self) -> PingResult:
        """Make a minimal API call to verify auth + reachability + model availability.

        Returns
        -------
        PingResult
            Always returns; encodes failure via ``ok=False`` rather than raising,
            so the CLI doctor can format a single-line status uniformly.
        """

    @abstractmethod
    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        max_tokens: int = 1024,
    ) -> CompleteResult:
        """Produce a single completion for the given system prompt and message history.

        Parameters
        ----------
        system : str
            The system prompt; provider implementations should prompt-cache this.
        messages : list[Message]
            Chat history; must alternate ``user`` / ``assistant`` and start with ``user``.
        max_tokens : int, optional
            Cap on output tokens (default 1024).
        """

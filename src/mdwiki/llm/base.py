"""Provider abstract base class — the seam that lets v1.0.0 swap to local models on Day 2.

A ``Provider`` wraps a single LLM backend (Anthropic in v1.0.0; Qwen, Kimi, etc. in
later releases). Every provider exposes the same surface: ``ping`` for health checks,
``complete`` for one-shot inference, and (since v1.1.0 Phase 5) ``describe_image`` for
vision-based extraction. Vision is opt-in: the default implementation raises
``NotImplementedError`` so providers without multimodal support can inherit cleanly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


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
    """The outcome of one ``complete()`` call — surface metrics needed for cost tracking.

    ``tool_input`` is populated when the call was made with ``tools`` and the
    model responded via ``tool_use`` (constrained-decoding mode). Callers that
    pass tools should read ``tool_input`` instead of ``text`` — the schema
    guarantees a parsed dict matching the tool's ``input_schema``.
    """

    text: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    tool_input: dict[str, Any] | None = None


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
    supports_batch: bool = False
    supports_tool_use: bool = False

    def is_recoverable_error(self, exc: Exception) -> bool:
        """Return whether ``exc`` is isolated enough to continue with the next source.

        Providers opt in only transport/rate-limit/server errors that may vary by
        request. Authentication, model, permission, and request-shape errors must
        remain fatal so bulk ingest does not repeat a systemic failure.
        """
        return False

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
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
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
        tools : list[dict], optional
            Tool definitions in mdwiki's provider-adapter format. When provided alongside
            ``tool_choice``, the provider engages constrained decoding so the
            response payload is structurally guaranteed to conform to the tool's
            ``input_schema``. Result is exposed in ``CompleteResult.tool_input``.
            Providers without constrained-decoding support may ignore this
            argument and fall back to free-form text generation; orchestration
            checks ``supports_tool_use`` before supplying it.
        tool_choice : dict, optional
            Forces the model to call a specific tool when set, e.g.
            ``{"type": "tool", "name": "submit_plan"}``. Ignored when ``tools``
            is None.
        """

    def describe_image(self, image_path: Path) -> str:
        """Describe an image as markdown text via the provider's vision API.

        Used by ``ImageLoader`` (Phase 5) and ``PdfLoader`` vision_fallback to extract
        text and structural information from raster images. The default raises
        ``NotImplementedError`` so providers without multimodal support can inherit
        cleanly — concrete providers override only when their model handles vision.

        Parameters
        ----------
        image_path : Path
            Filesystem path to a raster image (png/jpg/jpeg/webp/gif).

        Returns
        -------
        str
            Markdown text describing the image (typically a heading + a paragraph
            of description plus any extracted text via OCR).
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support describe_image. "
            "Use a vision-capable provider (e.g. AnthropicProvider with claude-sonnet-4-6+) "
            "or set [loaders.image].enabled = false in your wiki config."
        )

    def batch_complete(
        self,
        requests: list[BatchRequest],
        *,
        poll_interval: float = 60.0,
        on_status: Callable[[str, int, int], None] | None = None,
        on_batch_id: Callable[[str], None] | None = None,
    ) -> list[BatchResult]:
        """Submit a list of completion requests as a single batch (50% cheaper, ~1h ETA).

        Default raises ``NotImplementedError`` — providers without batch APIs
        (most local servers) inherit cleanly.

        Parameters
        ----------
        requests : list[BatchRequest]
            One request per pending source. ``custom_id`` matches results to sources.
        poll_interval : float, optional
            Seconds between status polls. Default 60s; tests pass 0.0 to spin fast.
        on_status : callable, optional
            Called as ``(status, succeeded, total)`` after each poll so the CLI can
            report progress.
        on_batch_id : callable, optional
            Called once with the provider-side batch id immediately after submission,
            so callers (e.g. ``BootstrapResult``) can surface it for diagnostics.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support batch_complete. "
            "Use the sync ingest path (`mdwiki init --bootstrap`) or configure "
            "bootstrap-batch to fall back to synchronous ingest."
        )

    def estimate_batch_cost(self, requests: list[BatchRequest]) -> BatchCostEstimate:
        """Return a coarse upper-bound cost estimate for the given batch.

        Default uses a 4-chars-per-token heuristic over the prompt text plus
        ``max_tokens`` per request as the output cap, then multiplies by the
        provider-specific batch rates. Concrete providers override when more
        accurate token counting is available.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not implement estimate_batch_cost.")


@dataclass(frozen=True)
class BatchRequest:
    """One pending completion in a batch submission.

    Parameters
    ----------
    custom_id : str
        Caller-chosen identifier (typically the source_id) — used to match
        results back to the originating source.
    system : str
        System prompt (provider implementations should still cache this).
    messages : list[Message]
        Chat history, same shape as ``Provider.complete``.
    max_tokens : int
        Per-request output cap.
    tools, tool_choice : optional
        Same semantics as ``Provider.complete`` — forwarded to the underlying
        provider's batch params so each request engages constrained decoding.
    """

    custom_id: str
    system: str
    messages: list[Message]
    max_tokens: int = 16000
    tools: list[dict[str, Any]] | None = None
    tool_choice: dict[str, Any] | None = None


@dataclass(frozen=True)
class BatchResult:
    """One outcome from a batch — success or failure.

    Parameters
    ----------
    custom_id : str
        Mirrors the ``BatchRequest.custom_id``.
    text : str
        Completion text. Empty when ``error`` is set or the response was a
        ``tool_use`` (in which case ``tool_input`` holds the parsed payload).
    error : str | None
        Provider-side error code (e.g. ``"rate_limited"``, ``"invalid_request"``)
        or ``None`` on success.
    input_tokens : int
        Prompt tokens billed for this result.
    output_tokens : int
        Output tokens billed for this result.
    tool_input : dict | None
        Parsed payload from a ``tool_use`` response; populated when the
        originating ``BatchRequest`` carried ``tools`` and the model chose to
        call one. ``None`` for free-form text responses.
    """

    custom_id: str
    text: str
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    tool_input: dict[str, Any] | None = None


@dataclass(frozen=True)
class BatchCostEstimate:
    """Coarse upper-bound cost estimate for a batch submission, in USD."""

    requests: int
    input_tokens: int
    output_tokens_max: int
    usd_total: float

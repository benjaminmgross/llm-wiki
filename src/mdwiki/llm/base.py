"""Provider abstract base class — the seam that lets v1.0.0 swap to local models on Day 2.

A ``Provider`` wraps a single LLM backend (Anthropic in v1.0.0; Qwen, Kimi, etc. in
later releases). Every provider exposes the same surface: ``ping`` for health checks,
``complete`` for one-shot inference, and (since v1.1.0 Phase 5) ``describe_image`` for
vision-based extraction. Vision is opt-in: the default implementation raises
``NotImplementedError`` so providers without multimodal support can inherit cleanly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
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

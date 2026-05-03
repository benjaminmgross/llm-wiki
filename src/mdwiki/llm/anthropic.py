"""Anthropic provider — wraps the official ``anthropic`` SDK for v1.0.0.

The system prompt is sent inside a list block with ``cache_control`` so subsequent
ingest calls share the schema/instructions cache. Errors from the SDK are caught
and translated into ``PingResult`` for ``mdwiki doctor``; ``complete()`` lets them
propagate so the caller (ingest, query) can decide how to surface them.

v1.1.0 Phase 5 added ``describe_image`` for the vision-OCR path used by
``ImageLoader`` and ``PdfLoader`` vision_fallback. Claude Sonnet 4.6 supports
the ``image`` content block on the messages API.
"""

from __future__ import annotations

import base64
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anthropic
import httpx

from mdwiki.llm.base import (
    BatchCostEstimate,
    BatchRequest,
    BatchResult,
    CompleteResult,
    Message,
    PingResult,
    Provider,
)

# Anthropic Sonnet 4.6 pricing (USD per million tokens). Batch API discount is 50%.
# Source: https://www.anthropic.com/pricing
# Last verified: 2026-04-30. TODO: re-check annually — these are hardcoded and
# will silently drift if Anthropic adjusts list pricing.
_SONNET_INPUT_USD_PER_MTOK: float = 3.0
_SONNET_OUTPUT_USD_PER_MTOK: float = 15.0
_BATCH_DISCOUNT: float = 0.5
# Coarse estimate: 4 chars per token (English text). Used as an upper bound for
# cost estimation only — the real billing uses Anthropic's tokenizer.
_CHARS_PER_TOKEN: float = 4.0

_IMAGE_MEDIA_TYPES: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

_DESCRIBE_IMAGE_PROMPT: str = (
    "Describe this image as a wiki page would describe it. Include:\n"
    "1. A short title-cased heading naming what the image shows.\n"
    "2. A paragraph describing the visual content (what kind of image it is, structure, layout).\n"
    "3. Any visible text from the image transcribed verbatim under a `### Text` subsection.\n"
    "Use markdown. Be concise but complete — this output will be ingested into a knowledge base."
)


class MissingAPIKeyError(RuntimeError):
    """Raised when ``ANTHROPIC_API_KEY`` is unset and no explicit ``api_key`` was passed."""


class OutputTruncatedError(RuntimeError):
    """Raised when the model hit ``max_tokens`` mid-response (truncated output)."""


class BatchTimeoutError(RuntimeError):
    """Raised when a batch poll exceeds the wall-clock cap (24h, matches Anthropic SLA).

    The batch may still be in-flight server-side. The user can inspect or cancel
    it via the Anthropic console using the batch id surfaced in the message.
    """


class BatchUnexpectedStatusError(RuntimeError):
    """Raised when the batch enters a terminal/abnormal status (canceling, expired, errored).

    These statuses indicate the batch will not produce results we can apply; we
    surface a clear error rather than polling forever.
    """


# 24 hours — matches Anthropic's published Batch API SLA. A batch that hasn't
# ended by then is almost certainly stuck; force the caller to investigate.
_BATCH_WALL_CLOCK_TIMEOUT_SECONDS: float = 24 * 60 * 60


class AnthropicProvider(Provider):
    """Concrete provider backed by Anthropic's Messages API."""

    name: str = "anthropic"

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        client: anthropic.Anthropic | None = None,
    ) -> None:
        """Initialize the provider.

        Parameters
        ----------
        model : str
            Anthropic model id (e.g. ``"claude-sonnet-4-6"``).
        api_key : str, optional
            Explicit API key; if absent, reads ``ANTHROPIC_API_KEY`` from the environment.
        client : anthropic.Anthropic, optional
            Pre-built SDK client; mainly used by tests to inject a mock.

        Raises
        ------
        MissingAPIKeyError
            If neither ``api_key`` nor ``ANTHROPIC_API_KEY`` is available.
        """
        self.model = model
        if client is not None:
            self._client = client
            return
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise MissingAPIKeyError(
                "ANTHROPIC_API_KEY is not set. Export it in your shell "
                "(`export ANTHROPIC_API_KEY=sk-...`) or pass api_key= explicitly."
            )
        # Bounded timeouts prevent a stalled socket from hanging --all forever;
        # max_retries=2 gives us SDK-level exponential backoff on 5xx/429 for free.
        self._client = anthropic.Anthropic(
            api_key=resolved_key,
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0),
            max_retries=2,
        )

    def ping(self) -> PingResult:
        """Issue a 1-token call to confirm auth, model, and reachability."""
        start = time.perf_counter()
        try:
            self._client.messages.create(
                model=self.model,
                max_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
            )
        except anthropic.AuthenticationError as exc:
            return PingResult(
                provider=self.name,
                model=self.model,
                latency_ms=_elapsed_ms(start),
                ok=False,
                message=f"auth failed — check ANTHROPIC_API_KEY ({exc.message})",
            )
        except anthropic.NotFoundError as exc:
            return PingResult(
                provider=self.name,
                model=self.model,
                latency_ms=_elapsed_ms(start),
                ok=False,
                message=f"model not found — verify '{self.model}' is a valid id ({exc.message})",
            )
        except anthropic.APIConnectionError as exc:
            return PingResult(
                provider=self.name,
                model=self.model,
                latency_ms=_elapsed_ms(start),
                ok=False,
                message=f"network/connection error — {exc}",
            )
        except anthropic.APIStatusError as exc:
            return PingResult(
                provider=self.name,
                model=self.model,
                latency_ms=_elapsed_ms(start),
                ok=False,
                message=f"API error ({exc.status_code}) — {exc.message}",
            )
        return PingResult(
            provider=self.name,
            model=self.model,
            latency_ms=_elapsed_ms(start),
            ok=True,
            message="pong",
        )

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        max_tokens: int = 1024,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> CompleteResult:
        """Issue a single completion request with prompt caching on the system prompt.

        Streams the response so high ``max_tokens`` requests don't risk SDK HTTP
        timeouts, then collects the final message via the SDK helper.

        When ``tools`` is provided, engages constrained decoding — the model's
        output is structurally guaranteed to conform to the chosen tool's
        ``input_schema``. The parsed payload is exposed in
        ``CompleteResult.tool_input``; ``CompleteResult.text`` holds any
        free-form text the model also emitted (typically empty when
        ``tool_choice`` forces a specific tool).

        Raises
        ------
        OutputTruncatedError
            If ``stop_reason`` is ``"max_tokens"`` — the response is truncated and
            cannot be safely parsed as JSON. Caller should retry with a higher cap.
        """
        if tools is None and tool_choice is not None:
            raise ValueError("tool_choice requires tools to be set")
        stream_kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if tools is not None:
            stream_kwargs["tools"] = tools
            if tool_choice is not None:
                stream_kwargs["tool_choice"] = tool_choice
        with self._client.messages.stream(**stream_kwargs) as stream:
            response = stream.get_final_message()

        if getattr(response, "stop_reason", None) == "max_tokens":
            raise OutputTruncatedError(
                f"Model hit max_tokens={max_tokens} mid-response. The output is truncated and likely invalid JSON. "
                f"Re-run with a higher --max-tokens (or trim the source if it's enormous)."
            )
        text = next((block.text for block in response.content if getattr(block, "type", None) == "text"), "")
        tool_input: dict[str, Any] | None = None
        if tools is not None:
            tool_block = next(
                (block for block in response.content if getattr(block, "type", None) == "tool_use"),
                None,
            )
            if tool_block is not None:
                # SDK exposes the parsed tool input as the ``input`` attribute (already a dict).
                raw_input = getattr(tool_block, "input", None)
                if isinstance(raw_input, dict):
                    tool_input = raw_input
        usage = response.usage
        return CompleteResult(
            text=text,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            tool_input=tool_input,
        )


    def describe_image(self, image_path: Path) -> str:
        """Send the image to Claude vision and return the model's markdown description.

        Uses ``messages.create`` (one-shot, not streamed) since the response is
        small (~200–600 tokens). The image is base64-encoded inline rather than
        uploaded — Anthropic's API supports base64 image blocks up to ~5MB.

        Raises
        ------
        ValueError
            If the file extension isn't a recognized image format.
        """
        suffix = image_path.suffix.lower()
        media_type = _IMAGE_MEDIA_TYPES.get(suffix)
        if media_type is None:
            raise ValueError(
                f"describe_image: unsupported image extension {suffix!r}. "
                f"Supported: {sorted(_IMAGE_MEDIA_TYPES)}"
            )
        b64 = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
        response = self._client.messages.create(
            model=self.model,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": media_type, "data": b64},
                        },
                        {"type": "text", "text": _DESCRIBE_IMAGE_PROMPT},
                    ],
                }
            ],
        )
        return next((block.text for block in response.content if getattr(block, "type", None) == "text"), "")


    def estimate_batch_cost(self, requests: list[BatchRequest]) -> BatchCostEstimate:
        """Coarse upper-bound cost estimate using a 4-chars-per-token heuristic.

        Output token count uses ``max_tokens`` (worst case). Real billing uses
        Anthropic's tokenizer and the actual output length, so the actual cost
        is typically lower.
        """
        input_chars = sum(
            len(req.system) + sum(len(m.content) for m in req.messages) for req in requests
        )
        input_tokens = int(input_chars / _CHARS_PER_TOKEN)
        output_tokens_max = sum(req.max_tokens for req in requests)
        usd_total = (
            (input_tokens * _SONNET_INPUT_USD_PER_MTOK / 1_000_000)
            + (output_tokens_max * _SONNET_OUTPUT_USD_PER_MTOK / 1_000_000)
        ) * _BATCH_DISCOUNT
        return BatchCostEstimate(
            requests=len(requests),
            input_tokens=input_tokens,
            output_tokens_max=output_tokens_max,
            usd_total=usd_total,
        )

    def batch_complete(
        self,
        requests: list[BatchRequest],
        *,
        poll_interval: float = 60.0,
        on_status: Callable[[str, int, int], None] | None = None,
        on_batch_id: Callable[[str], None] | None = None,
    ) -> list[BatchResult]:
        """Submit one batch via ``client.messages.batches`` and poll until complete.

        Returns one ``BatchResult`` per ``BatchRequest``. Failed/canceled results
        come back with ``error`` populated and empty ``text`` — caller decides
        how to surface them (typically: leave the source as ``pending`` and
        log a warning).
        """
        sdk_requests = []
        for req in requests:
            if req.tools is None and req.tool_choice is not None:
                raise ValueError("tool_choice requires tools to be set")
            params: dict[str, Any] = {
                "model": self.model,
                "max_tokens": req.max_tokens,
                "system": [
                    {"type": "text", "text": req.system, "cache_control": {"type": "ephemeral"}}
                ],
                "messages": [{"role": m.role, "content": m.content} for m in req.messages],
            }
            if req.tools is not None:
                params["tools"] = req.tools
                if req.tool_choice is not None:
                    params["tool_choice"] = req.tool_choice
            sdk_requests.append({"custom_id": req.custom_id, "params": params})
        batch = self._client.messages.batches.create(requests=sdk_requests)
        batch_id = batch.id
        if on_batch_id is not None:
            on_batch_id(batch_id)

        # 24h wall-clock cap matches Anthropic's Batch API SLA. Unknown / abnormal
        # statuses (canceling, expired, errored) are treated as terminal — they
        # mean we won't get usable results, so polling further is pointless.
        deadline = time.monotonic() + _BATCH_WALL_CLOCK_TIMEOUT_SECONDS
        while True:
            batch = self._client.messages.batches.retrieve(batch_id)
            counts = getattr(batch, "request_counts", None)
            status = batch.processing_status
            if on_status is not None:
                succeeded = getattr(counts, "succeeded", 0) if counts else 0
                on_status(status, succeeded, len(requests))
            if status == "ended":
                break
            if status in ("canceling", "canceled", "expired", "errored"):
                raise BatchUnexpectedStatusError(
                    f"Batch {batch_id} entered status {status!r} — it will not produce "
                    f"applicable results. Inspect or retry via the Anthropic console."
                )
            if time.monotonic() >= deadline:
                raise BatchTimeoutError(
                    f"Batch {batch_id} did not finish within "
                    f"{_BATCH_WALL_CLOCK_TIMEOUT_SECONDS / 3600:.0f}h "
                    f"(last status: {status!r}). Cancel or inspect it via the Anthropic console."
                )
            time.sleep(poll_interval)

        results: list[BatchResult] = []
        for entry in self._client.messages.batches.results(batch.id):
            results.append(_decode_batch_entry(entry))
        return results


def _decode_batch_entry(entry: object) -> BatchResult:
    """Convert one SDK batch-result entry into a ``BatchResult`` (success or error)."""
    custom_id = getattr(entry, "custom_id", "")
    result_obj = getattr(entry, "result", None)
    result_type = getattr(result_obj, "type", None)
    if result_type == "succeeded":
        message = getattr(result_obj, "message", None)
        content_blocks = getattr(message, "content", []) or []
        text = next((b.text for b in content_blocks if getattr(b, "type", None) == "text"), "")
        tool_input: dict[str, Any] | None = None
        tool_block = next((b for b in content_blocks if getattr(b, "type", None) == "tool_use"), None)
        if tool_block is not None:
            raw_input = getattr(tool_block, "input", None)
            if isinstance(raw_input, dict):
                tool_input = raw_input
        usage = getattr(message, "usage", None)
        return BatchResult(
            custom_id=custom_id,
            text=text,
            error=None,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            tool_input=tool_input,
        )
    # errored / canceled / expired: surface the type as the error code
    error_obj = getattr(result_obj, "error", None)
    error_msg = getattr(error_obj, "type", None) or result_type or "unknown_error"
    return BatchResult(custom_id=custom_id, text="", error=error_msg)


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0

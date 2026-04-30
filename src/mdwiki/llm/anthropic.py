"""Anthropic provider — wraps the official ``anthropic`` SDK for v1.0.0.

The system prompt is sent inside a list block with ``cache_control`` so subsequent
ingest calls share the schema/instructions cache. Errors from the SDK are caught
and translated into ``PingResult`` for ``mdwiki doctor``; ``complete()`` lets them
propagate so the caller (ingest, query) can decide how to surface them.
"""

from __future__ import annotations

import os
import time

import anthropic
import httpx

from mdwiki.llm.base import CompleteResult, Message, PingResult, Provider


class MissingAPIKeyError(RuntimeError):
    """Raised when ``ANTHROPIC_API_KEY`` is unset and no explicit ``api_key`` was passed."""


class OutputTruncatedError(RuntimeError):
    """Raised when the model hit ``max_tokens`` mid-response (truncated output)."""


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
    ) -> CompleteResult:
        """Issue a single completion request with prompt caching on the system prompt.

        Streams the response so high ``max_tokens`` requests don't risk SDK HTTP
        timeouts, then collects the final message via the SDK helper.

        Raises
        ------
        OutputTruncatedError
            If ``stop_reason`` is ``"max_tokens"`` — the response is truncated and
            cannot be safely parsed as JSON. Caller should retry with a higher cap.
        """
        with self._client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": m.role, "content": m.content} for m in messages],
        ) as stream:
            response = stream.get_final_message()

        if getattr(response, "stop_reason", None) == "max_tokens":
            raise OutputTruncatedError(
                f"Model hit max_tokens={max_tokens} mid-response. The output is truncated and likely invalid JSON. "
                f"Re-run with a higher --max-tokens (or trim the source if it's enormous)."
            )
        text = next((block.text for block in response.content if getattr(block, "type", None) == "text"), "")
        usage = response.usage
        return CompleteResult(
            text=text,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0

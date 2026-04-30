"""Generic OpenAI-compatible provider — works against vLLM, llama.cpp, OpenRouter, Together, etc.

Any endpoint that speaks OpenAI's chat-completions wire format works here: configure
``base_url`` to point at it, optionally ``api_key`` (vLLM ignores it; OpenRouter and
hosted providers require it), and ``model`` to name the model id the endpoint serves.

Vision is opt-in via ``vision_capable=True`` — only set this for models known to
support image inputs (e.g. ``qwen2.5-vl-72b``, ``llava-*``). The default raises
``NotImplementedError`` so misconfigured wikis fail loudly instead of silently
degrading to no-vision behavior.

Batch is unsupported: ``batch_complete`` raises ``NotImplementedError``. Most local
servers don't have a batch API; users wanting batch must switch to ``anthropic``.
"""

from __future__ import annotations

import base64
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import openai

from mdwiki.llm.base import (
    BatchRequest,
    BatchResult,
    CompleteResult,
    Message,
    PingResult,
    Provider,
)

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
    "2. A paragraph describing the visual content.\n"
    "3. Any visible text from the image transcribed verbatim under a `### Text` subsection.\n"
    "Use markdown."
)


class OpenAICompatibleProvider(Provider):
    """Provider backed by any OpenAI-compatible chat-completions endpoint."""

    name: str = "openai-compatible"

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 120.0,
        vision_capable: bool = False,
        client: Any | None = None,
    ) -> None:
        """Build a provider pointed at ``base_url``.

        Parameters
        ----------
        model : str
            Model id the endpoint serves (e.g. ``"qwen2.5-72b-instruct"``).
        base_url : str
            OpenAI-compatible endpoint root (e.g. ``"http://localhost:8000/v1"``).
        api_key : str, optional
            API key. vLLM and llama.cpp ignore this; OpenRouter / Together require it.
            Defaults to ``"not-needed"`` when None so the SDK doesn't refuse to construct.
        timeout : float, optional
            Request timeout in seconds (default 120).
        vision_capable : bool, optional
            Set True for models that accept image inputs (Qwen-VL, LLaVA). The
            default ``describe_image`` raises NotImplementedError; with this flag
            set, ``describe_image`` sends an image content block.
        client : openai.OpenAI, optional
            Pre-built SDK client (test injection). When omitted, builds one from
            ``base_url`` + ``api_key`` + ``timeout``.
        """
        self.model = model
        self.base_url = base_url
        self._vision_capable = vision_capable
        if client is not None:
            self._client = client
            return
        # vLLM and llama.cpp ignore the key, but the openai SDK refuses to build
        # without one — pass a placeholder when none is configured.
        self._client = openai.OpenAI(
            api_key=api_key or "not-needed",
            base_url=base_url,
            timeout=timeout,
        )

    def ping(self) -> PingResult:
        """Issue a 1-token call to verify the endpoint is reachable + serving the model."""
        start = time.perf_counter()
        try:
            self._client.chat.completions.create(
                model=self.model,
                max_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
            )
        except Exception as exc:  # noqa: BLE001 — we want one ping that never raises
            return PingResult(
                provider=self.name,
                model=self.model,
                latency_ms=_elapsed_ms(start),
                ok=False,
                message=str(exc),
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
        """Translate to OpenAI's chat-completions format and call the endpoint."""
        sdk_messages = [{"role": "system", "content": system}] + [
            {"role": m.role, "content": m.content} for m in messages
        ]
        response = self._client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            messages=sdk_messages,
        )
        text = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        return CompleteResult(
            text=text,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )

    def describe_image(self, image_path: Path) -> str:
        """Send the image to chat.completions if ``vision_capable=True``, else NotImplementedError.

        Most OpenAI-compatible endpoints serving non-vision models will reject image
        content blocks with an opaque error. The opt-in flag forces explicit user
        acknowledgement that the configured model supports vision.
        """
        if not self._vision_capable:
            raise NotImplementedError(
                f"describe_image requires vision_capable=True. Set [llm.openai_compatible].vision_capable = true "
                f"in config — only when model={self.model!r} actually supports image inputs."
            )
        suffix = image_path.suffix.lower()
        media_type = _IMAGE_MEDIA_TYPES.get(suffix)
        if media_type is None:
            raise ValueError(
                f"describe_image: unsupported image extension {suffix!r}. "
                f"Supported: {sorted(_IMAGE_MEDIA_TYPES)}"
            )
        b64 = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
        # OpenAI vision content format: an image_url block with a data: URI
        response = self._client.chat.completions.create(
            model=self.model,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}},
                        {"type": "text", "text": _DESCRIBE_IMAGE_PROMPT},
                    ],
                }
            ],
        )
        return response.choices[0].message.content or ""

    def batch_complete(
        self,
        requests: list[BatchRequest],
        *,
        poll_interval: float = 60.0,
        on_status: Callable[[str, int, int], None] | None = None,
    ) -> list[BatchResult]:
        """Most OpenAI-compatible local servers (vLLM, llama.cpp) don't expose a batch API."""
        raise NotImplementedError(
            "openai-compatible provider does not support batch_complete. "
            "Use the sync ingest path (`mdwiki init --bootstrap`), or switch "
            "to provider = 'anthropic' for the Batch API."
        )


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0

"""
Generate summaries for sections using Claude.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import anthropic


class Summarizer:
    """Generate summaries using Claude LLM."""

    def __init__(
        self,
        model: str = "claude-sonnet-4-5-20250929",
        api_key: str | None = None,
        timeout: float = 30.0,
        max_workers: int = 10,
    ):
        """
        Initialize summarizer.

        Parameters
        ----------
        model : str
            Claude model name.
        api_key : str | None
            Anthropic API key. If None, uses ANTHROPIC_API_KEY env var.
        timeout : float
            Request timeout in seconds.
        max_workers : int
            Maximum parallel API calls (default: 10).
        """
        self.model = model
        self.timeout = timeout
        self.max_workers = max_workers
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client: anthropic.Anthropic | None = None

    @property
    def client(self) -> anthropic.Anthropic:
        """Lazy-initialize the Anthropic client."""
        if self._client is None:
            self._client = anthropic.Anthropic(
                api_key=self._api_key,
                timeout=self.timeout,
            )
        return self._client

    def _generate(self, prompt: str) -> str | None:
        """Generate text from Claude."""
        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=256,
                messages=[
                    {"role": "user", "content": prompt}
                ],
            )
            return message.content[0].text.strip()
        except Exception:
            return None

    def _summarize_one(self, section: dict[str, Any]) -> tuple[int, str | None]:
        """Summarize a single section. Returns (index, summary)."""
        heading = section.get('heading', '')
        content = section.get('content', '')[:1000]  # Limit context

        prompt = f"""Summarize this documentation section in one sentence (max 100 characters).

Section: {heading}

Content:
{content}

Summary:"""

        return self._generate(prompt)

    def summarize_sections(
        self,
        sections: list[dict[str, Any]],
        *,
        verbose: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Add summaries to sections using parallel processing.

        Parameters
        ----------
        sections : list[dict]
            List of section dicts with 'content' and 'heading' keys.
        verbose : bool
            Print progress updates.

        Returns
        -------
        list[dict]
            Same sections with 'summary' key added.
        """
        if not sections:
            return sections

        if verbose:
            print(f"    Summarizing {len(sections)} sections...")

        completed = 0
        results = {}

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks
            future_to_idx = {
                executor.submit(self._summarize_one, section): idx
                for idx, section in enumerate(sections)
            }

            # Collect results as they complete
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result()
                except Exception:
                    results[idx] = None

                completed += 1
                if verbose and completed % 20 == 0:
                    print(f"    Summarized {completed}/{len(sections)} sections...")

        if verbose:
            print(f"    Summarized {completed}/{len(sections)} sections... done")

        # Apply results to sections
        for idx, section in enumerate(sections):
            section['summary'] = results.get(idx)

        return sections

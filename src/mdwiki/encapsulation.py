"""
Score header-content encapsulation using semantic similarity.
"""

from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import anthropic
import numpy as np
from sentence_transformers import SentenceTransformer


class EncapsulationScorer:
    """Score how well headers describe their content."""

    def __init__(
        self,
        *,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        threshold: float = 0.5,
    ):
        """
        Initialize encapsulation scorer.

        Parameters
        ----------
        model_name : str
            HuggingFace model name for sentence-transformers.
        threshold : float
            Score below which a section is considered poorly encapsulated.
        """
        self.model = SentenceTransformer(model_name)
        self.threshold = threshold

    def score_encapsulation(self, *, header: str, content: str) -> float:
        """
        Compute encapsulation score between header and content.

        Parameters
        ----------
        header : str
            The section header text.
        content : str
            The section content text.

        Returns
        -------
        float
            Score between 0.0 and 1.0, higher means better encapsulation.
        """
        if not header or not content:
            return 0.0

        # Truncate content to model's context window
        content_truncated = content[:2000]

        # Encode both as vectors
        embeddings = self.model.encode(
            [header, content_truncated],
            convert_to_numpy=True,
        )

        header_emb = embeddings[0]
        content_emb = embeddings[1]

        # Cosine similarity
        dot_product = np.dot(header_emb, content_emb)
        norms = np.linalg.norm(header_emb) * np.linalg.norm(content_emb)

        if norms < 1e-10:
            return 0.0

        return float(dot_product / norms)

    def encapsulate_sections(
        self,
        sections: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Add encapsulation scores to all sections.

        Parameters
        ----------
        sections : list[dict[str, Any]]
            List of section dicts with 'heading' and 'content' keys.

        Returns
        -------
        list[dict[str, Any]]
            Same sections with 'encapsulation_score' key added.
        """
        if not sections:
            return sections

        for section in sections:
            section['encapsulation_score'] = self.score_encapsulation(
                header=section.get('heading', ''),
                content=section.get('content', ''),
            )

        return sections


class Rechunker:
    """Split poorly-encapsulated sections using LLM."""

    def __init__(
        self,
        *,
        model: str = "claude-sonnet-4-5-20250929",
        api_key: str | None = None,
        timeout: float = 60.0,
        threshold: float = 0.5,
        max_workers: int = 10,
    ):
        """
        Initialize rechunker.

        Parameters
        ----------
        model : str
            Claude model name.
        api_key : str | None
            Anthropic API key. If None, uses ANTHROPIC_API_KEY env var.
        timeout : float
            Request timeout in seconds.
        threshold : float
            Encapsulation score below which to rechunk.
        max_workers : int
            Maximum parallel API calls (default: 10).
        """
        self.model = model
        self.timeout = timeout
        self.threshold = threshold
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
                max_tokens=2048,
                messages=[
                    {"role": "user", "content": prompt}
                ],
            )
            return message.content[0].text.strip()
        except Exception:
            return None

    def rechunk_section(
        self,
        section: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Split a poorly-encapsulated section into multiple sections.

        Parameters
        ----------
        section : dict[str, Any]
            Section dict with encapsulation_score.

        Returns
        -------
        list[dict[str, Any]]
            List of new sections (or original if well-encapsulated).
        """
        score = section.get('encapsulation_score', 1.0)
        if score >= self.threshold:
            return [section]

        content = section.get('content', '')[:3000]
        heading = section.get('heading', '')

        prompt = f"""Analyze this documentation section and split it into logical sub-sections.

Current Header: {heading}

Content:
{content}

This section mixes multiple topics. Split it into 2-4 focused sections.

Return ONLY a JSON array with objects containing "header" and "content" keys:
[{{"header": "New Header 1", "content": "Content for section 1..."}}, ...]

JSON:"""

        response = self._generate(prompt)
        if not response:
            return [section]

        try:
            # Strip markdown code fences if present
            response_clean = response.strip()
            if response_clean.startswith('```'):
                # Remove opening fence (```json or ```)
                response_clean = response_clean.split('\n', 1)[1] if '\n' in response_clean else ''
            if response_clean.endswith('```'):
                # Remove closing fence
                response_clean = response_clean.rsplit('```', 1)[0]
            response_clean = response_clean.strip()

            # Parse JSON from response
            parsed = json.loads(response_clean)
            if not isinstance(parsed, list) or len(parsed) < 2:
                return [section]

            # Create new sections
            new_sections = []
            for i, item in enumerate(parsed):
                new_content = item.get('content', '')
                # Compute fingerprint for the new content
                normalized = ' '.join(new_content.lower().split())
                fingerprint = hashlib.md5(normalized.encode()).hexdigest()

                new_sections.append({
                    'section_id': f"{section['section_id']}/{i+1}",
                    'heading': item.get('header', f"Part {i+1}"),
                    'content': new_content,
                    'source_file': section.get('source_file', ''),
                    'fingerprint': fingerprint,
                    'last_modified': section.get('last_modified', ''),
                    'rechunked_from': section['section_id'],
                })

            return new_sections

        except (json.JSONDecodeError, KeyError, TypeError):
            return [section]

    def rechunk_sections(
        self,
        sections: list[dict[str, Any]],
        *,
        verbose: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Rechunk all poorly-encapsulated sections using parallel processing.

        Parameters
        ----------
        sections : list[dict[str, Any]]
            Sections with encapsulation_score.
        verbose : bool
            Print progress updates.

        Returns
        -------
        list[dict[str, Any]]
            Sections with poor ones split into multiple.
        """
        # ============================================================
        # STEP 1: TRIAGE - Separate sections by encapsulation score
        # ============================================================
        # Sections with score >= threshold are well-encapsulated (header matches content)
        # Sections with score < threshold need rechunking (header doesn't describe content)
        needs_rechunk = []
        keep_as_is = []

        for section in sections:
            score = section.get('encapsulation_score', 1.0)
            if score < self.threshold:
                needs_rechunk.append(section)
            else:
                keep_as_is.append(section)

        if verbose:
            print(f"    {len(needs_rechunk)} sections need rechunking, {len(keep_as_is)} kept as-is")

        # Early exit if nothing needs rechunking
        if not needs_rechunk:
            return sections

        # ============================================================
        # STEP 2: PARALLEL PROCESSING - Submit all API calls at once
        # ============================================================
        # Instead of processing sections one-by-one (slow), we use a thread pool
        # to make multiple Claude API calls simultaneously (10 at a time by default)
        #
        # Example: 358 sections with 10 workers = ~36 batches instead of 358 sequential calls
        results = {}  # Dict to store results: {index: [rechunked_sections]}
        completed = 0

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all rechunking tasks to the thread pool
            # Each task calls self.rechunk_section(section) which makes a Claude API call
            # future_to_idx maps each Future object to its original index (to preserve order)
            future_to_idx = {
                executor.submit(self.rechunk_section, section): idx
                for idx, section in enumerate(needs_rechunk)
            }

            # ============================================================
            # STEP 3: COLLECT RESULTS - Gather results as they complete
            # ============================================================
            # as_completed() yields futures as they finish (not in submission order)
            # This is efficient because we don't wait for slow calls to block fast ones
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]  # Get original index for this result
                try:
                    # future.result() returns the list of rechunked sections
                    # (could be 1 original section, or 2-4 new split sections)
                    results[idx] = future.result()
                except Exception:
                    # On any error, keep the original section unchanged
                    results[idx] = [needs_rechunk[idx]]

                # Progress logging every 10 sections
                completed += 1
                if verbose and completed % 10 == 0:
                    print(f"    Rechunked {completed}/{len(needs_rechunk)} sections...")

        if verbose:
            print(f"    Rechunked {completed}/{len(needs_rechunk)} sections... done")

        # ============================================================
        # STEP 4: RECONSTRUCT - Rebuild the sections list in order
        # ============================================================
        # Results came back out-of-order (as_completed), so we iterate by index
        # to maintain the original document order
        result = []
        for idx in range(len(needs_rechunk)):
            result.extend(results[idx])  # Each result is a list of 1+ sections

        # Add back sections that didn't need rechunking (score >= threshold)
        result.extend(keep_as_is)

        return result

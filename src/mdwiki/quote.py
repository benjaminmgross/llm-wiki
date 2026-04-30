"""Quote-anchor verification — pure Python, no LLM call.

Every claim in an LLM ingest plan must (a) reference a real source section id and
(b) contain a verbatim quote that appears in the source text. Whitespace runs are
normalized before matching so line-wrapped quotes still match; case is folded so
the LLM doesn't fail validation over an incidental capital. Quotes shorter than
``MIN_QUOTE_WORDS`` words are rejected — they aren't anchors, they're noise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from mdwiki.plan import Claim, Plan

MIN_QUOTE_WORDS: int = 4


@dataclass(frozen=True)
class ClaimError:
    """A single rejected claim and why."""

    claim: Claim
    message: str


@dataclass(frozen=True)
class QuoteVerificationResult:
    """Outcome of ``verify_plan``."""

    valid: bool
    errors: tuple[ClaimError, ...]


def verify_plan(plan: Plan, *, source_text: str, section_ids: set[str]) -> QuoteVerificationResult:
    """Check every claim against the source.

    Parameters
    ----------
    plan : Plan
        Parsed LLM plan.
    source_text : str
        The raw text of the source being ingested.
    section_ids : set[str]
        The set of valid section ids the LLM is allowed to cite.
    """
    normalized_source = _normalize(source_text)
    errors: list[ClaimError] = []

    for claim in plan.all_claims():
        if claim.source_section_id not in section_ids:
            errors.append(ClaimError(claim=claim, message=f"unknown source_section_id: {claim.source_section_id!r}"))
            continue
        if len(claim.quote.split()) < MIN_QUOTE_WORDS:
            errors.append(
                ClaimError(claim=claim, message=f"quote too short ({len(claim.quote.split())} words; minimum {MIN_QUOTE_WORDS})")
            )
            continue
        if _normalize(claim.quote) not in normalized_source:
            errors.append(ClaimError(claim=claim, message=f"quote not found in source: {claim.quote!r}"))

    return QuoteVerificationResult(valid=not errors, errors=tuple(errors))


_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Collapse whitespace to single spaces and lowercase. Idempotent."""
    return _WHITESPACE_RE.sub(" ", text).strip().lower()

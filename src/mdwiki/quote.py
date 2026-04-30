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

    Section ids are also normalized before comparison — the LLM strips markdown
    formatting and unicode oddities from headings when echoing them, so an
    exact-match comparison would reject correctly-cited sections too.

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
    normalized_section_ids = {_normalize(sid) for sid in section_ids}
    errors: list[ClaimError] = []

    for claim in plan.all_claims():
        if _normalize(claim.source_section_id) not in normalized_section_ids:
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
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_MD_EMPHASIS_RE = re.compile(r"(\*{1,3}|_{1,3})(\S(?:.*?\S)?)\1")
_NON_ALNUM_RE = re.compile(r"[^\w\s]")
_UNICODE_PUNCT_FOLDS = str.maketrans(
    {
        "‘": "'",  # ‘
        "’": "'",  # ’
        "‚": "'",  # ‚
        "‛": "'",  # ‛
        "“": '"',  # “
        "”": '"',  # ”
        "„": '"',  # „
        "‟": '"',  # ‟
        "–": "-",  # – (en dash)
        "—": "-",  # — (em dash)
        "―": "-",  # ― (horizontal bar)
        "…": "...",  # …
        " ": " ",  # non-breaking space
    }
)


def _normalize(text: str) -> str:
    """Bring source and quote into a comparable shape: prose, lowercased, words only.

    The order is load-bearing. Markdown link extraction first (so we keep the
    visible text and drop the URL); inline-code and emphasis next (so we strip
    the delimiters but keep the inner content); Unicode punctuation fold next
    (curly quotes → straight, em/en-dashes → hyphen) so the LLM's plain-ASCII
    extract still matches the original's typography; finally collapse all
    remaining punctuation to spaces and squeeze whitespace.
    """
    out = _MD_LINK_RE.sub(r"\1", text)
    out = _MD_INLINE_CODE_RE.sub(r"\1", out)
    out = _MD_EMPHASIS_RE.sub(r"\2", out)
    out = out.translate(_UNICODE_PUNCT_FOLDS)
    out = _NON_ALNUM_RE.sub(" ", out)
    return _WHITESPACE_RE.sub(" ", out).strip().lower()

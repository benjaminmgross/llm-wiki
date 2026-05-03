"""Quote-anchor verification — pure Python, no LLM call.

Every claim in an LLM ingest plan must (a) reference a real source section id and
(b) contain a verbatim quote that appears in the source text. Whitespace runs are
normalized before matching so line-wrapped quotes still match; case is folded so
the LLM doesn't fail validation over an incidental capital. Quotes shorter than
``MIN_QUOTE_WORDS`` words are rejected — they aren't anchors, they're noise.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from mdwiki.plan import Claim, Plan

VALID_NORMALIZE_MODES: frozenset[str] = frozenset({"default", "transcripts"})

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


def verify_plan(
    plan: Plan,
    *,
    source_text: str,
    section_ids: set[str],
    mode: str = "default",
    min_quote_words: int = MIN_QUOTE_WORDS,
) -> QuoteVerificationResult:
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
    mode : str, default "default"
        Normalization mode applied to BOTH source and quote text. ``"default"``
        is the legacy prose-aware normalization. ``"transcripts"`` additionally
        strips ``[HH:MM:SS]``/``[HH:MM]`` timestamps and ``Speaker Name:``
        line prefixes so a quote extracted mid-utterance still anchors when
        the source has speaker attribution prepended. Section-id normalization
        always uses ``"default"`` — section ids are headings, not transcript
        lines.
    min_quote_words : int, default MIN_QUOTE_WORDS
        Minimum word count for a quote anchor. Sparse-text corpora (HTML
        decks, slide exports) may need a lower floor since the LLM cannot
        produce 4-word verbatim spans against bullet fragments. Reads from
        ``[ingest].min_quote_words`` in the wiki's config via
        ``min_quote_words_for_wiki``. Hard floor enforced upstream.
    """
    normalized_source = _normalize(source_text, mode=mode)
    normalized_section_ids = {_normalize(sid) for sid in section_ids}
    errors: list[ClaimError] = []

    for claim in plan.all_claims():
        if _normalize(claim.source_section_id) not in normalized_section_ids:
            errors.append(ClaimError(claim=claim, message=f"unknown source_section_id: {claim.source_section_id!r}"))
            continue
        if len(claim.quote.split()) < min_quote_words:
            errors.append(
                ClaimError(claim=claim, message=f"quote too short ({len(claim.quote.split())} words; minimum {min_quote_words})")
            )
            continue
        if _normalize(claim.quote, mode=mode) not in normalized_source:
            errors.append(ClaimError(claim=claim, message=f"quote not found in source: {claim.quote!r}"))

    return QuoteVerificationResult(valid=not errors, errors=tuple(errors))


_WHITESPACE_RE = re.compile(r"\s+")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_MD_EMPHASIS_RE = re.compile(r"(\*{1,3}|_{1,3})(\S(?:.*?\S)?)\1")
_NON_ALNUM_RE = re.compile(r"[^\w\s]")
# Transcripts mode: strip ``[HH:MM:SS]`` and ``[HH:MM]`` timestamps before
# the rest of the normalization runs.
_TIMESTAMP_RE = re.compile(r"\[\d{1,2}:\d{2}(?::\d{2})?\]")
# Transcripts mode: strip leading ``Speaker Name:`` prefixes (anchored to
# start of line) so a quote extracted mid-utterance still anchors when the
# source has speaker attribution prepended. Capped at 40 characters and
# limited to letter-shaped sequences so we don't accidentally swallow
# regular sentences with a colon mid-line.
_SPEAKER_PREFIX_RE = re.compile(r"(?m)^[A-Z][A-Za-z .'\-]{0,40}:\s+")
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


def _normalize(text: str, *, mode: str = "default") -> str:
    """Bring source and quote into a comparable shape: prose, lowercased, words only.

    Parameters
    ----------
    text : str
        Source or quote text to normalize.
    mode : str, default "default"
        ``"default"`` is the legacy prose-aware normalization. ``"transcripts"``
        additionally strips ``[HH:MM:SS]``/``[HH:MM]`` timestamps and
        ``Speaker Name:`` line prefixes before the default normalization
        runs, so a quote extracted mid-utterance still anchors against a
        source that has speaker attribution prepended.

    Returns
    -------
    str
        Normalized lowercased prose with whitespace runs collapsed.

    Notes
    -----
    The order is load-bearing. Transcript-specific stripping happens FIRST
    (timestamps and speaker prefixes are removed before any markdown
    handling sees them); then markdown link extraction (keep visible text,
    drop URL); then inline-code and emphasis (strip delimiters, keep
    content); then Unicode punctuation fold (curly quotes -> straight,
    em/en-dashes -> hyphen); finally collapse remaining punctuation to
    spaces and squeeze whitespace.

    The space-padded markdown substitutions (``" \\1 "``) also apply when
    this function is called on section ids (``verify_plan`` normalizes both
    section_ids and the cited source_section_id through here). That's safe
    because section ids are derived from headings, where intra-word emphasis
    butted against adjacent text is rare; the doubled-space output is
    collapsed by the trailing whitespace squeeze.
    """
    out = text
    if mode == "transcripts":
        out = _TIMESTAMP_RE.sub(" ", out)
        out = _SPEAKER_PREFIX_RE.sub("", out)
    # Substitute with surrounding spaces so the captured content keeps its word
    # boundaries when the markdown delimiter abuts adjacent prose. Empirical
    # case from luxe-give HTML decks: ``**1.7x revenue growth**3.6x`` — a bare
    # substitution would yield ``1.7x revenue growth3.6x`` (no space), and the
    # LLM's natural quote ``1.7x revenue growth 3.6x`` would never substring-
    # match. The trailing whitespace collapse normalizes the doubled spaces.
    out = _MD_LINK_RE.sub(r" \1 ", out)
    out = _MD_INLINE_CODE_RE.sub(r" \1 ", out)
    out = _MD_EMPHASIS_RE.sub(r" \2 ", out)
    out = out.translate(_UNICODE_PUNCT_FOLDS)
    out = _NON_ALNUM_RE.sub(" ", out)
    return _WHITESPACE_RE.sub(" ", out).strip().lower()


def min_quote_words_for_wiki(wiki_root: Path) -> int:
    """Return the wiki's configured minimum quote-word count.

    Reads ``.mdwiki/config.toml`` looking for ``[ingest].min_quote_words``.
    Returns ``MIN_QUOTE_WORDS`` when the config doesn't declare a value, the
    file is missing, the value isn't a positive integer, or the value is
    less than 1 (the hard floor — a 0-word minimum would disable the check
    entirely).

    Sparse-text corpora (HTML decks, slide exports) may need a lower floor
    than the default 4 since the LLM cannot produce 4-word verbatim spans
    against bullet fragments. Mirrors the structure of
    ``quote_normalize_mode_for_wiki``.

    Parameters
    ----------
    wiki_root : Path
        Directory containing ``.mdwiki/config.toml``.

    Returns
    -------
    int
        Minimum word count to pass to ``verify_plan``. Always >= 1.
    """
    config_path = wiki_root / ".mdwiki" / "config.toml"
    if not config_path.is_file():
        return MIN_QUOTE_WORDS
    try:
        config = tomllib.loads(config_path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return MIN_QUOTE_WORDS
    declared = config.get("ingest", {}).get("min_quote_words")
    if not isinstance(declared, int) or isinstance(declared, bool) or declared < 1:
        return MIN_QUOTE_WORDS
    return declared


def quote_normalize_mode_for_wiki(wiki_root: Path) -> str:
    """Return the wiki's profile-declared quote normalization mode.

    Reads ``.mdwiki/config.toml`` looking for
    ``[profile.<name>].quote_normalize_mode``. Returns ``"default"`` when the
    config doesn't declare a mode (most profiles), or when the declared mode
    is not one of ``VALID_NORMALIZE_MODES``. Mirrors the structure of
    ``mdwiki.plan.allowed_kinds_for_wiki``.

    Parameters
    ----------
    wiki_root : Path
        Directory containing ``.mdwiki/config.toml``.

    Returns
    -------
    str
        Normalization mode to pass to ``verify_plan``. Always one of
        ``VALID_NORMALIZE_MODES``.
    """
    import tomllib

    config_path = wiki_root / ".mdwiki" / "config.toml"
    if not config_path.is_file():
        return "default"
    try:
        config = tomllib.loads(config_path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return "default"
    profile_name = config.get("profile", {}).get("name")
    if not profile_name:
        return "default"
    declared = config.get("profile", {}).get(profile_name, {}).get("quote_normalize_mode")
    if not isinstance(declared, str) or declared not in VALID_NORMALIZE_MODES:
        return "default"
    return declared

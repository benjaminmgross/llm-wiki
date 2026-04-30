"""Strict parser for the LLM ingest response.

The LLM returns a JSON object describing what to do with a source. We parse it
into immutable dataclasses so downstream code (quote verification, diff render,
transactional apply) consumes a typed shape rather than nested dicts.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

VALID_KINDS: frozenset[str] = frozenset({"entity", "concept", "synthesis"})
VALID_BARE_VERDICTS: frozenset[str] = frozenset({"ingest", "low-quality", "out-of-scope"})

# Page paths the LLM proposes must be confined to wiki/. Anything else
# (.mdwiki/config.toml, raw/<hash>.md, /etc/passwd) is rejected at parse time
# so we fail fast — before opening a transaction or touching disk.
_REQUIRED_PAGE_PREFIX: str = "wiki/"


class PlanValidationError(ValueError):
    """Raised when the LLM's JSON response does not conform to the expected plan schema."""


@dataclass(frozen=True)
class Claim:
    """One supporting quote from the source for a wiki edit."""

    source_section_id: str
    quote: str


@dataclass(frozen=True)
class Update:
    """A full-page rewrite of an existing wiki page.

    ``content`` is the COMPLETE revised page body — the apply step writes it
    verbatim to ``page`` (no section splicing). The LLM is responsible for
    preserving any text it doesn't intend to change. v1.0.0 deliberately
    skips section-aware splicing (see C10 in the v1.0.0 review): full-page
    rewrites are simpler, more reliable, and avoid markdown-AST parsing.
    """

    page: str
    content: str
    claims: tuple[Claim, ...]


@dataclass(frozen=True)
class NewPage:
    """A brand-new wiki page proposed by the LLM."""

    path: str
    kind: str
    content: str
    claims: tuple[Claim, ...]


@dataclass(frozen=True)
class CrossRef:
    """A directional link to add between two wiki pages."""

    from_page: str
    to_page: str
    anchor_text: str


@dataclass(frozen=True)
class Plan:
    """The full LLM plan for one source ingest."""

    verdict: str
    rationale: str
    updates: tuple[Update, ...]
    new_pages: tuple[NewPage, ...]
    cross_refs: tuple[CrossRef, ...]

    def all_claims(self) -> Iterator[Claim]:
        """Yield every ``Claim`` across both updates and new pages — the verification target."""
        for update in self.updates:
            yield from update.claims
        for new_page in self.new_pages:
            yield from new_page.claims

    def is_empty(self) -> bool:
        """True when no edits, pages, or cross-refs were proposed."""
        return not (self.updates or self.new_pages or self.cross_refs)


def parse_plan(raw_json: str) -> Plan:
    """Parse the LLM's JSON response into a typed ``Plan``.

    Tolerates the model occasionally wrapping the JSON in markdown code fences
    or surrounding it with prose preamble/postamble — extracts the outermost
    ``{...}`` block before parsing.

    Parameters
    ----------
    raw_json : str
        The raw text the LLM produced.

    Raises
    ------
    PlanValidationError
        If the JSON is malformed or any required structure is missing or invalid.
    """
    cleaned = _extract_json_object(raw_json)
    try:
        payload: Any = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise PlanValidationError(f"Invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise PlanValidationError(f"Expected JSON object, got {type(payload).__name__}.")

    verdict = _require(payload, "verdict", str)
    _validate_verdict(verdict)

    rationale = _require(payload, "rationale", str)
    updates = tuple(_parse_update(u) for u in _require(payload, "updates", list))
    new_pages = tuple(_parse_new_page(p) for p in _require(payload, "new_pages", list))
    cross_refs = tuple(_parse_cross_ref(r) for r in _require(payload, "cross_refs", list))

    if verdict != "ingest" and (updates or new_pages or cross_refs):
        raise PlanValidationError(
            f"verdict {verdict!r} must be paired with empty updates/new_pages/cross_refs; got non-empty."
        )

    for update in updates:
        _validate_page_path(update.page, field="updates[].page")
    for new_page in new_pages:
        _validate_page_path(new_page.path, field="new_pages[].path")

    return Plan(verdict=verdict, rationale=rationale, updates=updates, new_pages=new_pages, cross_refs=cross_refs)


def _validate_page_path(path: str, *, field: str) -> None:
    """Reject any LLM-proposed page path that escapes ``wiki/``.

    Defense in depth: ``transaction.write_file`` will also block writes outside
    ``wiki_root``, but doing the check here means a malicious or confused LLM
    response is rejected BEFORE we open a transaction or touch disk. Without
    this, an LLM-supplied ``.mdwiki/config.toml`` could overwrite the user's
    config, or ``../../../etc/passwd`` could trigger an uncaught ValueError
    deep inside the apply loop and abort the whole bulk run.
    """
    if not path.startswith(_REQUIRED_PAGE_PREFIX):
        raise PlanValidationError(
            f"{field}={path!r} must start with {_REQUIRED_PAGE_PREFIX!r}; LLM-supplied page paths are confined to wiki/."
        )
    if ".." in path.split("/"):
        raise PlanValidationError(
            f"{field}={path!r} contains a '..' segment; path traversal is not allowed."
        )


def _extract_json_object(raw: str) -> str:
    """Return the substring from the first ``{`` to the last ``}`` after stripping fences.

    Strips ``` ``` ``` and ``` ```json ``` ``` wrappings, then snips off any prose
    preamble/postamble around the outermost JSON object.
    """
    text = raw.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[: -len("```")]
    first = text.find("{")
    last = text.rfind("}")
    if first == -1 or last == -1 or last < first:
        return text  # no JSON object found; let json.loads produce the error
    return text[first : last + 1]


def _validate_verdict(verdict: str) -> None:
    if verdict in VALID_BARE_VERDICTS:
        return
    if verdict.startswith("duplicate-of:") and len(verdict) > len("duplicate-of:"):
        return
    raise PlanValidationError(
        f"Invalid verdict {verdict!r}. Must be one of {sorted(VALID_BARE_VERDICTS)} or 'duplicate-of:<page-path>'."
    )


def _parse_update(raw: Any) -> Update:
    if not isinstance(raw, dict):
        raise PlanValidationError(f"Expected update object, got {type(raw).__name__}.")
    return Update(
        page=_require(raw, "page", str),
        content=_require(raw, "content", str),
        claims=tuple(_parse_claim(c) for c in _require(raw, "claims", list)),
    )


def _parse_new_page(raw: Any) -> NewPage:
    if not isinstance(raw, dict):
        raise PlanValidationError(f"Expected new_page object, got {type(raw).__name__}.")
    kind = _require(raw, "kind", str)
    if kind not in VALID_KINDS:
        raise PlanValidationError(f"Invalid page kind {kind!r}. Must be one of {sorted(VALID_KINDS)}.")
    return NewPage(
        path=_require(raw, "path", str),
        kind=kind,
        content=_require(raw, "content", str),
        claims=tuple(_parse_claim(c) for c in _require(raw, "claims", list)),
    )


def _parse_cross_ref(raw: Any) -> CrossRef:
    if not isinstance(raw, dict):
        raise PlanValidationError(f"Expected cross_ref object, got {type(raw).__name__}.")
    return CrossRef(
        from_page=_require(raw, "from_page", str),
        to_page=_require(raw, "to_page", str),
        anchor_text=_require(raw, "anchor_text", str),
    )


def _parse_claim(raw: Any) -> Claim:
    if not isinstance(raw, dict):
        raise PlanValidationError(f"Expected claim object, got {type(raw).__name__}.")
    return Claim(
        source_section_id=_require(raw, "source_section_id", str),
        quote=_require(raw, "quote", str),
    )


def _require(payload: dict, key: str, expected_type: type) -> Any:
    if key not in payload:
        raise PlanValidationError(f"Missing required field {key!r}.")
    value = payload[key]
    if not isinstance(value, expected_type):
        raise PlanValidationError(
            f"Field {key!r} must be {expected_type.__name__}, got {type(value).__name__}."
        )
    return value

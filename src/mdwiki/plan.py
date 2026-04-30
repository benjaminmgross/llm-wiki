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


class PlanValidationError(ValueError):
    """Raised when the LLM's JSON response does not conform to the expected plan schema."""


@dataclass(frozen=True)
class Claim:
    """One supporting quote from the source for a wiki edit."""

    source_section_id: str
    quote: str


@dataclass(frozen=True)
class Update:
    """An edit to an existing wiki page section."""

    page: str
    section: str
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

    Parameters
    ----------
    raw_json : str
        The raw text the LLM produced.

    Raises
    ------
    PlanValidationError
        If the JSON is malformed or any required structure is missing or invalid.
    """
    try:
        payload: Any = json.loads(raw_json)
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

    return Plan(verdict=verdict, rationale=rationale, updates=updates, new_pages=new_pages, cross_refs=cross_refs)


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
        section=_require(raw, "section", str),
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

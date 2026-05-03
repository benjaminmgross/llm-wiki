"""Strict parser for the LLM ingest response.

The LLM returns a JSON object describing what to do with a source. We parse it
into immutable dataclasses so downstream code (quote verification, diff render,
transactional apply) consumes a typed shape rather than nested dicts.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Baseline page kinds always allowed by every profile. Extra kinds declared
# by a profile's config-overlay (``[profile.<name>].extra_page_kinds``) are
# unioned in by ``allowed_kinds_for_wiki()`` at plan-validation time. We keep
# the baseline frozenset for callers that don't have a wiki context (e.g.
# pure unit tests of ``parse_plan``) — those use the legacy validation.
VALID_KINDS: frozenset[str] = frozenset({"entity", "concept", "synthesis"})
VALID_BARE_VERDICTS: frozenset[str] = frozenset({"ingest", "low-quality", "out-of-scope"})


def allowed_kinds_for_wiki(wiki_root: Path) -> frozenset[str]:
    """Return the union of baseline page kinds + the wiki's profile extras.

    Reads ``.mdwiki/config.toml`` looking for ``[profile.<name>].extra_page_kinds``.
    Returns ``VALID_KINDS`` unchanged when the config doesn't declare extras
    (e.g. working-dir profile or a pre-Phase-2 wiki).

    Parameters
    ----------
    wiki_root : Path
        Directory containing ``.mdwiki/config.toml``.

    Returns
    -------
    frozenset[str]
        Allowed page kinds for plans validated against this wiki.
    """
    import tomllib

    config_path = wiki_root / ".mdwiki" / "config.toml"
    if not config_path.is_file():
        return VALID_KINDS
    try:
        config = tomllib.loads(config_path.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return VALID_KINDS
    profile_name = config.get("profile", {}).get("name")
    if not profile_name:
        return VALID_KINDS
    extras = config.get("profile", {}).get(profile_name, {}).get("extra_page_kinds", [])
    if not isinstance(extras, list):
        return VALID_KINDS
    return frozenset(VALID_KINDS | {str(k) for k in extras})

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


def parse_plan(raw_json: str, *, allowed_kinds: frozenset[str] | None = None) -> Plan:
    """Parse the LLM's JSON response into a typed ``Plan``.

    Tolerates the model occasionally wrapping the JSON in markdown code fences
    or surrounding it with prose preamble/postamble — extracts the outermost
    ``{...}`` block before parsing.

    Use this entry point for *text* responses (legacy, also used by the
    cluster-verdict path in ``synthesize.py``). For ``tool_use`` responses
    where the SDK already exposes a parsed dict, prefer ``parse_plan_dict``
    to skip the redundant string round-trip.

    Parameters
    ----------
    raw_json : str
        The raw text the LLM produced.
    allowed_kinds : frozenset[str], optional
        Page kinds permitted in ``new_pages[].kind``. Defaults to ``VALID_KINDS``
        (entity / concept / synthesis). Profile-aware callers should pass the
        result of ``allowed_kinds_for_wiki(wiki_root)`` so profile-declared
        extras (e.g. framework's ``procedure``/``template``/``assessment``/
        ``learning``) are accepted.

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
    return parse_plan_dict(payload, allowed_kinds=allowed_kinds)


def parse_plan_dict(payload: dict[str, Any], *, allowed_kinds: frozenset[str] | None = None) -> Plan:
    """Validate and shape an already-parsed Plan payload into the typed dataclass.

    Used on the ``tool_use`` ingest path where the Anthropic SDK exposes the
    model's tool input as a parsed dict (constrained-decoding guarantees the
    JSON shape; we still validate field types and the verdict/non-empty
    invariants here).

    Parameters
    ----------
    payload : dict
        Already-parsed JSON object — either the SDK-exposed ``tool_use.input``
        or the result of ``json.loads`` upstream.
    allowed_kinds : frozenset[str], optional
        See ``parse_plan``.

    Raises
    ------
    PlanValidationError
        Same conditions as ``parse_plan``, minus the JSON-decode case.
    """
    if allowed_kinds is None:
        allowed_kinds = VALID_KINDS

    verdict = _require(payload, "verdict", str)
    _validate_verdict(verdict)

    rationale = _require(payload, "rationale", str)
    updates = tuple(_parse_update(u) for u in _require(payload, "updates", list))
    new_pages = tuple(_parse_new_page(p, allowed_kinds=allowed_kinds) for p in _require(payload, "new_pages", list))
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
    # Control characters (including \x00) can be used to slip a "..\\" segment
    # past the split-on-/ check below ("wiki/\x00../etc/passwd" splits to
    # ["wiki", "\x00..", "etc", "passwd"] — no ".." segment). Reject any
    # control byte up front; legitimate page paths never contain them.
    if any(ch < " " for ch in path):
        raise PlanValidationError(
            f"{field}={path!r} contains a control character; page paths must be plain ASCII/UTF-8 text."
        )
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


def _parse_new_page(raw: Any, *, allowed_kinds: frozenset[str] = VALID_KINDS) -> NewPage:
    if not isinstance(raw, dict):
        raise PlanValidationError(f"Expected new_page object, got {type(raw).__name__}.")
    kind = _require(raw, "kind", str)
    if kind not in allowed_kinds:
        raise PlanValidationError(f"Invalid page kind {kind!r}. Must be one of {sorted(allowed_kinds)}.")
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

"""Anthropic ``tool_use`` schema for the ingest plan.

When ingest runs against ``AnthropicProvider``, the model is forced to call
``submit_plan`` rather than emit free-form JSON in response text. The schema
below mirrors the ``Plan`` dataclass shape so the SDK-parsed ``tool_use.input``
feeds directly into ``parse_plan_dict`` without a string round-trip — and,
more importantly, the model's output is structurally guaranteed to be valid
JSON conforming to this schema (constrained decoding at the API layer).

The schema is an authoring contract, not the source of truth. The source of
truth remains the ``Plan`` dataclass + ``parse_plan_dict`` validation; this
schema is the upstream guard that keeps invalid shapes from ever reaching
the parser. Keep them in sync — when the ``Plan`` shape changes, update both.

``additionalProperties: False`` is set on every object schema below as an
intentional silent-drift guard: if the model emits a key we don't expect
(``"notes"``, ``"draft"``, etc.), constrained decoding rejects it instead of
quietly dropping it. If Anthropic's tool format ever adds optional metadata
keys the model is expected to set, we'll need to relax this — for now the
guard is more valuable than the forward-compat headroom.
"""

from __future__ import annotations

from typing import Any

# ``INGEST_TOOL_CHOICE.name`` and ``INGEST_TOOL_DEFINITION.name`` both derive
# from this constant; tests guard the round-trip.
INGEST_TOOL_NAME: str = "submit_plan"

_CLAIM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["source_section_id", "quote"],
    "properties": {
        "source_section_id": {
            "type": "string",
            "description": "The section id from the source — copy verbatim from the prompt's section list.",
        },
        "quote": {
            "type": "string",
            "description": (
                "Verbatim span from the source backing this claim. Must appear in the source text "
                "after whitespace normalization. Minimum word count is enforced downstream."
            ),
        },
    },
}

_UPDATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["page", "content", "claims"],
    "properties": {
        "page": {
            "type": "string",
            "description": "Path to an existing wiki page (e.g. ``wiki/entities/foo.md``).",
        },
        "content": {
            "type": "string",
            "description": "Replacement body for the page (full content, not a diff).",
        },
        "claims": {
            "type": "array",
            "items": _CLAIM_SCHEMA,
            "minItems": 1,
            "description": "Source-anchored claims supporting this update; one per substantive assertion.",
        },
    },
}

_NEW_PAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path", "kind", "content", "claims"],
    "properties": {
        "path": {
            "type": "string",
            "description": "Target path under ``wiki/`` (e.g. ``wiki/entities/foo.md``).",
        },
        "kind": {
            "type": "string",
            "description": (
                "Page kind — must be one of the wiki's allowed kinds (entity/concept/synthesis "
                "plus any profile extras). Validated downstream."
            ),
        },
        "content": {
            "type": "string",
            "description": "Initial page body.",
        },
        "claims": {
            "type": "array",
            "items": _CLAIM_SCHEMA,
            "minItems": 1,
        },
    },
}

_CROSS_REF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["from_page", "to_page", "anchor_text"],
    "properties": {
        "from_page": {"type": "string", "description": "Path to the source wiki page."},
        "to_page": {"type": "string", "description": "Path to the target wiki page."},
        "anchor_text": {
            "type": "string",
            "description": "Visible link text used when rendering the cross-reference.",
        },
    },
}

_CONTRADICTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["page", "existing_claim", "source_claim", "claims"],
    "properties": {
        "page": {"type": "string", "description": "Existing wiki page (or one written in this plan) that holds the disputed claim."},
        "existing_claim": {"type": "string", "description": "What the wiki page currently says, copied from the page."},
        "source_claim": {"type": "string", "description": "What this source says instead, in one sentence."},
        "resolution": {
            "type": "string",
            "enum": ["pending", "source-wins", "existing-wins", "both-hold"],
            "description": "Leave ``pending`` unless the source itself settles it (a newer date, an explicit correction).",
        },
        "claims": {
            "type": "array",
            "items": _CLAIM_SCHEMA,
            "minItems": 1,
            "description": "Verbatim source quotes backing source_claim; verified exactly like page claims.",
        },
    },
}

INGEST_TOOL_DEFINITION: dict[str, Any] = {
    "name": INGEST_TOOL_NAME,
    "description": (
        "Submit your wiki ingest plan for this source. Use the verdict ``ingest`` when "
        "the source warrants real wiki changes; use ``low-quality``, ``out-of-scope``, "
        "or ``duplicate-of:<page>`` to refuse cleanly. Refusing verdicts must come with "
        "empty updates/new_pages/cross_refs."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["verdict", "rationale", "updates", "new_pages", "cross_refs"],
        "properties": {
            "verdict": {
                "type": "string",
                # Constrain at the schema layer so the model can't invent verdicts
                # like "empty" / "skip" / "needs-review". The duplicate-of form
                # carries a dynamic page path so we use a pattern, not enum.
                "pattern": r"^(ingest|low-quality|out-of-scope|duplicate-of:.+)$",
                "description": (
                    "One of: ``ingest``, ``low-quality``, ``out-of-scope``, "
                    "or ``duplicate-of:<existing-wiki-page-path>``."
                ),
            },
            "rationale": {
                "type": "string",
                "description": "One- to three-sentence explanation of the verdict.",
            },
            "updates": {
                "type": "array",
                "items": _UPDATE_SCHEMA,
                "description": "Edits to existing wiki pages. Empty when verdict != ``ingest``.",
            },
            "new_pages": {
                "type": "array",
                "items": _NEW_PAGE_SCHEMA,
                "description": "New wiki pages to create. Empty when verdict != ``ingest``.",
            },
            "cross_refs": {
                "type": "array",
                "items": _CROSS_REF_SCHEMA,
                "description": "New cross-references between wiki pages. Empty when verdict != ``ingest``.",
            },
            "contradictions": {
                "type": "array",
                "items": _CONTRADICTION_SCHEMA,
                "description": (
                    "Disagreements between this source and existing pages. Record one instead of silently overwriting "
                    "or dropping the new claim. Optional; empty when verdict != ``ingest``."
                ),
            },
        },
    },
}

INGEST_TOOL_CHOICE: dict[str, Any] = {"type": "tool", "name": INGEST_TOOL_NAME}

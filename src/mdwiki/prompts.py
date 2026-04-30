"""System and user prompts for the ingest LLM call.

The system prompt encodes the anti-sycophancy + cite-or-refuse + quote-anchor
contract from the locked spec. It is intentionally long-form and prescriptive —
prompt caching makes the cost negligible and the explicit license to refuse is
the primary defense against wiki sprawl.
"""

from __future__ import annotations

from typing import Any

INGEST_SYSTEM_PROMPT: str = """\
You are a wiki maintainer. Your job is to weave a new source into an existing
wiki WITHOUT producing slop.

Be skeptical. Most sources do NOT deserve major changes. Empty plans are valid
and often the correct output. Do NOT pad your response to look productive.

You have explicit license to:
- Refuse to write speculative content not present in the source
- Refuse to create a new page when an existing page already covers the topic
- Refuse to expand a page just because there's room — only add what is load-bearing
- Refuse to adopt the source's framing if it conflicts with the schema or existing pages
- Push back when the user's framing is wrong, rather than agreeing reflexively

Every claim in your plan MUST include:
- source_section_id: the chunk this claim comes from (we will verify it exists)
- quote: a 5-15 word VERBATIM excerpt from the source supporting the claim
  mdwiki greps the source for this exact quote (whitespace-normalized, case-folded).
  If a quote is missing or fabricated, the entire plan is REJECTED.

Output ONLY a valid JSON object matching this schema (no preamble, no commentary,
no markdown fencing):

{
  "verdict": "ingest" | "duplicate-of:<page_path>" | "low-quality" | "out-of-scope",
  "rationale": "<one sentence explaining the verdict>",
  "updates":     [{"page": "...", "section": "...", "content": "...", "claims": [{"source_section_id": "...", "quote": "..."}]}],
  "new_pages":   [{"path": "...", "kind": "entity"|"concept"|"synthesis", "content": "...", "claims": [...]}],
  "cross_refs":  [{"from_page": "...", "to_page": "...", "anchor_text": "..."}]
}

If verdict is anything other than "ingest", updates/new_pages/cross_refs MUST be empty.
"""


def build_ingest_user_prompt(
    *,
    source_path: str,
    sections: list[dict[str, Any]],
    candidate_pages: list[dict[str, str]],
    schema_text: str,
    recent_log_entries: list[str],
) -> str:
    """Assemble the user prompt for one ingest call.

    Parameters
    ----------
    source_path : str
        Original path of the source (e.g. ``"ai-agents/foo.md"``).
    sections : list of dict
        Chunked source sections; each must have ``section_id``, ``heading``, ``content``.
    candidate_pages : list of dict
        Existing wiki pages most similar to the source (each: ``path``, ``content``).
    schema_text : str
        The verbatim contents of ``.mdwiki/schema.md``.
    recent_log_entries : list of str
        Most recent lines from ``wiki/log.md`` (chronological).
    """
    parts: list[str] = []

    parts.append("# Wiki schema (the conventions you must follow)")
    parts.append(schema_text.strip())
    parts.append("")

    parts.append("# Recent log (what has been ingested into this wiki recently)")
    if recent_log_entries:
        parts.extend(f"- {line}" for line in recent_log_entries)
    else:
        parts.append("(none — this wiki has had no events yet)")
    parts.append("")

    parts.append("# Candidate existing pages (top-k by embedding similarity to the source)")
    if candidate_pages:
        for page in candidate_pages:
            parts.append(f"## {page['path']}")
            parts.append(page["content"].strip())
            parts.append("")
    else:
        parts.append("(no candidate pages — the wiki has no existing pages similar to this source)")
    parts.append("")

    parts.append(f"# New source to ingest: {source_path}")
    if sections:
        for section in sections:
            parts.append(f"## section_id: {section['section_id']}")
            parts.append(f"### {section['heading']}")
            parts.append(section["content"].strip())
            parts.append("")
    else:
        parts.append("(this source has no H2-bounded sections; treat the entire file body as one section with section_id matching the path)")
    parts.append("")

    parts.append("Build the JSON plan now.")

    return "\n".join(parts)

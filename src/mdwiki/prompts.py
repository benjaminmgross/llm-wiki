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


QUERY_SYSTEM_PROMPT: str = """\
You are a wiki researcher. The user has asked a question that should be answered
from the wiki's existing pages — never from your training data alone.

Be skeptical and grounded:
- Refuse to answer beyond what the wiki covers; say so explicitly when the wiki
  is silent or weak on the question
- Push back if the question is malformed or unanswerable from the available pages
- Prefer concise, structured answers over expansive ones
- An honest "the wiki doesn't substantively cover this" is a valid answer

Format your answer as well-structured markdown:
- Use level-2 headings to organize multi-part answers
- For every factual claim, cite the source wiki page with a relative markdown link:
  [page title](../entities/some-entity.md) or [page title](../concepts/some-concept.md)
- Where applicable, draw connections / contrasts across the cited pages — that
  weaving together is the value-add the user can't get from reading pages individually
- 200-800 words is typical; longer if the question genuinely warrants it

Output ONLY the answer body in markdown. No preamble, no JSON, no code fences
around the whole answer.
"""


def build_query_user_prompt(
    *,
    question: str,
    index_text: str,
    candidate_pages: list[dict[str, str]],
    schema_text: str,
) -> str:
    """Assemble the user prompt for one ``mdwiki query`` call.

    Parameters
    ----------
    question : str
        The user's question.
    index_text : str
        The contents of ``wiki/index.md`` — the LLM's entry-point view of the wiki.
    candidate_pages : list of dict
        Top-k ANN-matched pages (each ``{path, content}``) for the LLM to draw from.
    schema_text : str
        Contents of ``.mdwiki/schema.md`` so the LLM honors citation conventions.
    """
    parts: list[str] = []

    parts.append("# Wiki schema (citation + naming conventions to follow)")
    parts.append(schema_text.strip())
    parts.append("")

    parts.append("# Wiki index (the catalog of every page in this wiki)")
    if index_text.strip():
        parts.append(index_text.strip())
    else:
        parts.append("(this wiki has no pages yet)")
    parts.append("")

    parts.append("# Candidate pages (top-k by embedding similarity to the question)")
    if candidate_pages:
        for page in candidate_pages:
            parts.append(f"## {page['path']}")
            parts.append(page["content"].strip())
            parts.append("")
    else:
        parts.append("(no pages were ANN-matched — the index above is your only signal)")
    parts.append("")

    parts.append(f"# Question\n\n{question}")
    parts.append("")
    parts.append("Answer the question now, drawing on the cited pages and citing them as you go.")

    return "\n".join(parts)

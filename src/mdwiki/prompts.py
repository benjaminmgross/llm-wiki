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

For an UPDATE, ``content`` is the COMPLETE revised page content — the entire
page body as you want it stored. mdwiki replaces the whole file with this
text. Preserve everything you don't intend to change. Do NOT send a
diff or section fragment.

Output ONLY a valid JSON object matching this schema (no preamble, no commentary,
no markdown fencing):

{
  "verdict": "ingest" | "duplicate-of:<page_path>" | "low-quality" | "out-of-scope",
  "rationale": "<one sentence explaining the verdict>",
  "updates":     [{"page": "...", "content": "<COMPLETE revised page content>", "claims": [{"source_section_id": "...", "quote": "..."}]}],
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


SYNTHESIZE_TOPIC_SYSTEM_PROMPT: str = """\
You are writing a synthesis page for a wiki — a cross-cutting writeup that
braids together what the wiki already knows about a topic.

A good synthesis page:
- Compares, contrasts, or weaves together multiple existing pages — it is NOT
  a transcription of one page nor a duplicate of an existing concept page
- Cites the pages it draws from with relative markdown links:
  [page title](../concepts/some-concept.md)
- Adds genuine value-over-the-parts: a decision framework, a comparison table,
  an architectural summary, or a tension/trade-off the individual pages don't
  surface on their own
- Is 300–800 words; longer if the topic genuinely warrants it
- Has a clear H1 title (the topic) and 2–4 H2 sections

Be skeptical:
- If the wiki doesn't cover the topic in enough depth to synthesize, say so
  honestly. Output the single line "INSUFFICIENT_COVERAGE: <why>" and stop.
- If the topic would just duplicate an existing concept page, say so:
  "DUPLICATE_OF: <page-path>" and stop.
- An empty/refused synthesis is a valid output.

Output ONLY the synthesis page body in markdown, OR one of the two refusal
prefixes above. No preamble, no JSON, no fences.
"""


def build_synthesize_topic_user_prompt(
    *,
    topic: str,
    candidate_pages: list[dict[str, str]],
    schema_text: str,
    index_text: str,
) -> str:
    """Assemble the user prompt for ``mdwiki synthesize <topic>``."""
    parts: list[str] = []

    parts.append("# Wiki schema (citation + naming conventions to follow)")
    parts.append(schema_text.strip())
    parts.append("")

    parts.append("# Wiki index (full catalog of pages)")
    if index_text.strip():
        parts.append(index_text.strip())
    else:
        parts.append("(this wiki has no pages yet)")
    parts.append("")

    parts.append("# Candidate pages most relevant to the topic")
    if candidate_pages:
        for page in candidate_pages:
            parts.append(f"## {page['path']}")
            parts.append(page["content"].strip())
            parts.append("")
    else:
        parts.append("(no candidate pages — output INSUFFICIENT_COVERAGE)")
    parts.append("")

    parts.append(f"# Topic for synthesis\n\n{topic}")
    parts.append("")
    parts.append("Write the synthesis page now, or refuse if the wiki doesn't substantively cover the topic.")

    return "\n".join(parts)


SYNTHESIZE_CLUSTER_SYSTEM_PROMPT: str = """\
You are evaluating whether a cluster of cross-referencing wiki pages would
benefit from a synthesis page that braids them together.

Be skeptical:
- Most clusters do NOT need a synthesis. Cross-references are usually enough.
- Only propose a synthesis when the cluster shares a non-obvious through-line
  that's worth its own page (a comparison, a decision framework, a unifying
  pattern, an inheritance/composition relationship).

Output STRICT JSON only, no preamble or fences:

{
  "propose": true | false,
  "title": "<short noun phrase, kebab-case-friendly>" | null,
  "rationale": "<one sentence explaining why this cluster does or does not warrant a synthesis>"
}

If propose is false, title must be null.
"""


def build_cluster_user_prompt(*, cluster_pages: list[dict[str, str]]) -> str:
    """Assemble the user prompt for one cluster-evaluation call in auto mode."""
    parts: list[str] = ["# Cluster of cross-referencing pages"]
    for page in cluster_pages:
        parts.append(f"## {page['path']}")
        parts.append(page["content"].strip())
        parts.append("")
    parts.append("Decide whether this cluster warrants a synthesis page.")
    return "\n".join(parts)

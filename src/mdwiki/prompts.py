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
wiki WITHOUT producing slop AND WITHOUT under-building it.

Be skeptical, but not paralyzed. The schema gives concrete touch-breadth targets
(5–15 wiki pages per ingest, typically) — read the schema before deciding.

You have explicit license to refuse — sparingly:
- Refuse to write speculative content not present in the source.
- Refuse to adopt the source's framing if it conflicts with the schema.
- Push back when the user's framing is wrong, rather than agreeing reflexively.

You also have an EXPLICIT MANDATE to create:
- CREATE an entity page for every named subject (person, project, paper, system,
  organization) the source provides 2+ substantive factual claims about — even
  when an existing concept page already mentions that subject. Entity pages are
  how the wiki avoids becoming a handful of bloated concept pages.
- CREATE cross-references between every new entity page and the concept pages
  that frame it. A new page with no inbound or outbound links is a lint failure.

`low-quality` and `out-of-scope` verdicts should be RARE. Topical overlap with
an existing concept page is NOT grounds for refusal — it warrants UPDATES + NEW
ENTITY PAGES. If you find yourself reaching for `low-quality` because "the
existing page already mentions this," re-read the schema's "When to refuse"
section and reconsider whether named subjects in the source deserve entity pages.

Empty plans are valid only when the source genuinely has no factual claims any
wiki page could cite. They are the EXCEPTION, not the default.

Every claim in your plan MUST include a source_section_id and a quote.

The source_section_id must be a chunk id from THIS source — the one named below
as "New source to ingest." Cross-source citation is not permitted here; even
when you draw on knowledge from other wiki pages, every claim's quote must come
from the current source. Use cross_refs for cross-source connections instead.

The quote must be a contiguous, verbatim excerpt from the source. Verbatim
means copy-paste exactly — no paraphrasing, no summarizing, no character
substitutions. Contiguous means a single span of consecutive text — no
ellipsis, no joining of non-adjacent passages. If you need to cite two
separate parts of the source, write two separate claims, one per quote.
Quotes must be at least 4 words long; ideal length is 5 to 15 words.

mdwiki greps the source for each quote (whitespace and case are normalized).
If any quote is missing, fabricated, contains an ellipsis, or violates the
length minimum, the entire plan is REJECTED. When uncertain, drop the
questionable claim — fewer verified claims always beat more unverified ones.

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

# Tool-use variant. Used when the provider supports constrained-decoding
# ``tool_use`` (Anthropic). Drops the inline JSON-shape spec — that spec was
# the model's only source of structure for free-form text responses, but with
# tool_use the schema lives in the tool definition (``ingest_tool.py``) and
# duplicating it in the prompt invites the model to reason about both shapes
# simultaneously, producing wrong-typed fields like ``updates: "<text>"``.
INGEST_SYSTEM_PROMPT_TOOL_USE: str = INGEST_SYSTEM_PROMPT.split(
    "Output ONLY a valid JSON object matching this schema", 1
)[0].rstrip() + (
    "\n\n"
    "Submit your plan by calling the ``submit_plan`` tool. Its input_schema is\n"
    "the source of truth for required fields and types — do not emit JSON in\n"
    "your text response. The tool's ``verdict`` accepts only ``ingest``,\n"
    "``low-quality``, ``out-of-scope``, or ``duplicate-of:<wiki/page/path.md>``.\n"
    "If verdict is anything other than ``ingest``, ``updates`` / ``new_pages`` /\n"
    "``cross_refs`` MUST be empty arrays.\n"
)


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

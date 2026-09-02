"""System and user prompts for the ingest LLM call.

The system prompt encodes the anti-sycophancy + cite-or-refuse + quote-anchor
contract from the locked spec. It is intentionally long-form and prescriptive —
prompt caching makes the cost negligible and the explicit license to refuse is
the primary defense against wiki sprawl.
"""

from __future__ import annotations

from typing import Any

MAX_QUOTE_BANK_LINES: int = 120

# Prose-guidance head: shared verbatim by both the JSON-output and tool-use
# variants. Ends right before the output-format spec so each variant can append
# its own spec block without overlap. Keep this string self-contained — never
# reference output format here; that lives in the per-variant tail blocks.
INGEST_SYSTEM_PROMPT_HEAD: str = """\
You are a wiki maintainer. Your job is to weave a new source into an existing
wiki WITHOUT producing slop AND WITHOUT under-building it.

Be skeptical, but not paralyzed. The schema gives concrete touch-breadth targets
for strong hosted models; for smaller/local models, a smaller VALID plan is
better than an ambitious invalid plan. Prefer 1–4 high-confidence page touches
with verified quotes over 5–15 page touches with any paraphrased quote.
When working from a long source, cap yourself at:
- at most 1 update
- at most 2 new_pages
- at most 1 claim per touched page
This cap is mandatory unless the user explicitly asks for exhaustive ingest.

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

STRICT LOCAL-MODEL QUOTE RULE:
- The `quote` field is evidence text only, not your claim.
- Do not turn source facts into a sentence. For example, if the source row says
  `CEO | Lino Maldonado | Ex-VP Wyndham`, the quote may be
  `Lino Maldonado | Ex-VP Wyndham`, but MUST NOT be
  `Lino Maldonado is Ex-VP Wyndham`.
- Use short copied spans from the source exactly as printed, including table
  wording and punctuation.
- The safest quote is one exact source line, one exact table cell, or one exact
  bullet phrase. Never merge multiple bullets, rows, or sentences into one
  quote. Never remove markdown markers from inside the copied span.
- If you cannot find a copied span that supports a page, omit that page.

mdwiki greps the source for each quote (whitespace and case are normalized).
If any quote is missing, fabricated, contains an ellipsis, joins separate
source spans, or violates the length minimum, the entire plan is REJECTED.
When uncertain, drop the questionable claim — one verified claim beats many
unverified claims.

For an UPDATE, ``content`` is the COMPLETE revised page content — the entire
page body as you want it stored. mdwiki replaces the whole file with this
text. Preserve everything you don't intend to change. Do NOT send a
diff or section fragment.

CONTRADICTIONS: when the source disagrees with a candidate page (a different
date, number, attribution, or conclusion), do NOT silently overwrite the page
and do NOT drop the new claim. Add a ``contradictions`` entry naming the page,
the existing claim (copied from the page), the source's claim, and at least
one verbatim source quote. Leave ``resolution`` as ``pending`` unless the
source itself settles the matter.

LANGUAGE: write page prose in the language of the source. Keep YAML keys,
``kind`` values, file names, and section headings in English.
"""

# Inline JSON-shape spec used by providers without constrained decoding. The
# model's only source of output structure is this block, so the spec must be
# explicit and complete. Composed onto ``INGEST_SYSTEM_PROMPT_HEAD`` below.
INGEST_SYSTEM_PROMPT_JSON_SPEC: str = """\

Output ONLY a valid JSON object matching this schema (no preamble, no commentary,
no markdown fencing):

{
  "verdict": "ingest" | "duplicate-of:<page_path>" | "low-quality" | "out-of-scope",
  "rationale": "<one sentence explaining the verdict>",
  "updates":     [{"page": "...", "content": "<COMPLETE revised page content>", "claims": [{"source_section_id": "...", "quote": "..."}]}],
  "new_pages":   [{"path": "...", "kind": "entity"|"concept"|"synthesis", "content": "...", "claims": [...]}],
  "cross_refs":  [{"from_page": "...", "to_page": "...", "anchor_text": "..."}],
  "contradictions": [{"page": "...", "existing_claim": "...", "source_claim": "...", "resolution": "pending"|"source-wins"|"existing-wins"|"both-hold", "claims": [...]}]
}

"contradictions" is optional (default empty). If verdict is anything other than "ingest", updates/new_pages/cross_refs/contradictions MUST be empty.
"""

# Tool-use tail. Used when the provider supports constrained-decoding
# ``tool_use`` (Anthropic). Drops the inline JSON-shape spec — that spec was
# the model's only source of structure for free-form text responses, but with
# tool_use the schema lives in the tool definition (``ingest_tool.py``) and
# duplicating it in the prompt invites the model to reason about both shapes
# simultaneously, producing wrong-typed fields like ``updates: "<text>"``.
INGEST_SYSTEM_PROMPT_TOOL_TAIL: str = """\

Submit your plan by calling the ``submit_plan`` tool. Its input_schema is
the source of truth for required fields and types — do not emit JSON in
your text response. The tool's ``verdict`` accepts only ``ingest``,
``low-quality``, ``out-of-scope``, or ``duplicate-of:<wiki/page/path.md>``.
If verdict is anything other than ``ingest``, ``updates`` / ``new_pages`` /
``cross_refs`` MUST be empty arrays.
"""

INGEST_SYSTEM_PROMPT: str = INGEST_SYSTEM_PROMPT_HEAD + INGEST_SYSTEM_PROMPT_JSON_SPEC
INGEST_SYSTEM_PROMPT_TOOL_USE: str = INGEST_SYSTEM_PROMPT_HEAD + INGEST_SYSTEM_PROMPT_TOOL_TAIL


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

    quote_bank = _quote_bank_for_sections(sections)
    parts.append("# Verified quote bank (copy `quote` values from here when possible)")
    if quote_bank:
        for item in quote_bank:
            parts.append(f"- section_id: {item['section_id']}")
            parts.append(f"  quote: {item['quote']}")
    else:
        parts.append("(no quote-bank excerpts were generated; copy exact spans from the source below)")
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

    parts.append(
        "Build the JSON plan now. Keep it small: at most 1 update, at most 2 new_pages, "
        "and at most 1 claim per touched page. Every quote must be copied from one exact "
        "source line, table cell, or bullet phrase; do not combine source spans."
    )

    return "\n".join(parts)


def _quote_bank_for_sections(sections: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Extract source lines likely to work as verbatim quote anchors."""
    bank: list[dict[str, str]] = []
    for section in sections:
        section_id = str(section["section_id"])
        for raw_line in str(section["content"]).splitlines():
            line = raw_line.strip()
            if not _looks_like_quote_bank_line(line):
                continue
            bank.append({"section_id": section_id, "quote": line})
            if len(bank) >= MAX_QUOTE_BANK_LINES:
                return bank
    return bank


def _looks_like_quote_bank_line(line: str) -> bool:
    if not line:
        return False
    if line.startswith(("```", "%%", "graph ", "style ", "subgraph ", "end")):
        return False
    if line in {"---", "|---|", "| --- |"}:
        return False
    words = [word for word in line.replace("|", " ").split() if word.strip("-*`")]
    if len(words) < 4:
        return False
    return True


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
- End with exactly one line of the form
  `Confidence: high|medium|low — <one clause>` where high means multiple
  corroborating pages, medium means a single page, and low means inference
  beyond what the pages state
- Write prose in the language the cited pages use; keep headings in English

If the wiki does not cover the question at all, output a single line starting
with `NO_COVERAGE:` followed by what is missing, and nothing else. mdwiki can
turn that into a stub page so the gap becomes visible.

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


OVERVIEW_SYSTEM_PROMPT: str = """\
You are writing the top-level overview page of a wiki — the first thing a reader
opens to orient themselves. You are given the wiki's schema, its index (every
page grouped by kind), its concept table (one row per page with source counts
and status), and the list of unresolved contradictions.

A good overview:
- Says in one paragraph what this corpus is about and who it is for
- Names the 3–7 main clusters or themes, each linking the pages that anchor it
  with relative markdown links: [page title](concepts/some-concept.md)
- Calls out open contradictions and thinly sourced areas so the reader knows
  where the wiki is weak
- Ends with a short "Start here" list of 3–5 pages
- Is 300–700 words with a clear H1 and 2–5 H2 sections

Be skeptical: if the wiki has too little content to summarize (fewer than three
substantive pages), output the single line "INSUFFICIENT_COVERAGE: <why>" and stop.
Write prose in the language the pages use; keep headings in English.

Output ONLY the overview page body in markdown, or the refusal line. No preamble,
no JSON, no fences around the whole page.
"""


def build_overview_user_prompt(
    *,
    schema_text: str,
    index_text: str,
    concept_table_text: str,
    pending_contradictions: list[str],
) -> str:
    """Assemble the user prompt for ``mdwiki overview``."""
    parts: list[str] = []
    parts.append("# Wiki schema (page kinds and conventions)")
    parts.append(schema_text.strip())
    parts.append("")
    parts.append("# Wiki index (every page by kind)")
    parts.append(index_text.strip() or "(this wiki has no pages yet)")
    parts.append("")
    parts.append("# Concept table (wiki/concept-table.md — sources, related pages, status per page)")
    parts.append(concept_table_text.strip() or "(empty)")
    parts.append("")
    parts.append("# Unresolved contradictions")
    if pending_contradictions:
        parts.extend(f"- {line}" for line in pending_contradictions)
    else:
        parts.append("(none recorded)")
    parts.append("")
    parts.append("Write the overview page now, or refuse with INSUFFICIENT_COVERAGE if the wiki is too thin.")
    return "\n".join(parts)

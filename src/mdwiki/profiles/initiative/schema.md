# mdwiki schema — `initiative` profile

The conventions and policies the LLM follows when ingesting sources into a
**project / strategy / cross-functional initiative folder** — typically with a
README at the root, subfolders organized by workstream, and mixed file types
(markdown for thinking, csv/xlsx for data, pdf/docx for evidence, optionally a
`transcripts/` subdir for meeting recordings).

Edit this file to steer your wiki's structure. The LLM rereads it on every
ingest, query, and synthesize call.

## Page kinds

| Kind | Folder | Purpose |
|---|---|---|
| `entity` | `wiki/entities/` | A specific person, organization, project, system, or named thing the initiative references |
| `concept` | `wiki/concepts/` | A reusable idea, pattern, or technique discussed across the initiative |
| `decision` | `wiki/decisions/` | A specific decision that was made, with the rationale, the alternatives considered, and the date |
| `status` | `wiki/status/` | Snapshot of where a workstream stands as of some date (point-in-time, never updated retroactively) |
| `workstream` | `wiki/workstreams/` | A long-running theme or sub-initiative inside the parent initiative |
| `owner` | `wiki/owners/` | A person or team accountable for one or more workstreams (one page per accountable party) |
| `synthesis` | `wiki/syntheses/` | A cross-cutting writeup that braids several pages together (e.g. "what we learned this quarter") |

## Naming

- Entities, concepts, owners, workstreams: lowercase-kebab-case, one topic per page
- Decisions: prefix with the date — `2026-04-30-pivot-to-property-management.md`
- Status pages: prefix with the date and workstream — `2026-04-30-funnel-status.md`
- Syntheses: short noun phrases — `q2-fundraise-takeaways.md`
- Always link to other pages with relative markdown links

## Citation format

Every claim in `wiki/` must be supported by a quote from a source in `raw/`.
Format:

```
[^src1]: <quote>... (raw/<hash>-<slug>.md, original: <original_path>)
```

## Touch breadth target

**5–15 wiki pages per source ingest** — typically:
- 1–3 page UPDATES
- 1–3 NEW entity pages (people, projects, organizations the source mentions with substance)
- 0–1 NEW decision page if the source records a decision being made
- 0–1 NEW status page if the source is a status update / progress report
- 3–8 cross-references

Plans touching ≤2 pages are almost always under-creating; reread the source
and ask which named subjects deserve their own pages.

## When to create entity pages

REQUIRED: when a source has 2+ substantive factual claims about a SPECIFIC
named person, organization, or project, create or update an entity page for
them. This is how an initiative wiki avoids becoming a handful of bloated
concept pages.

Heuristic: if you can write 3+ sentences about a named subject from the source,
they earn an entity page.

## When to create decision pages

A `decision` page captures a single decision with:

- The decision itself (a one-sentence summary)
- The date the decision was made
- The alternatives that were considered
- The rationale (why this option won)
- Cross-refs to the related concept / workstream / owner pages

Don't create a decision page for every meeting takeaway — only for moments
when a real choice was made between alternatives. Most "decisions" in
transcripts are actually status updates.

## When to create status pages

A `status` page is a **point-in-time snapshot** of a workstream. It captures:

- The workstream it pertains to (cross-ref)
- The date of the snapshot
- Current state (what's done, what's blocked, what's next)
- Owner (cross-ref)

**Status pages are immutable once written.** A new status page supersedes an
older one but never overwrites it. The wiki's history of status pages is the
trajectory of the workstream over time.

## When to create workstream pages

A `workstream` page describes a long-running theme inside the initiative —
e.g. "investor outreach," "due diligence package," "term-sheet negotiation"
inside a `capital-raise` initiative. One page per workstream. Workstream pages
are LIVING — they get updated as the workstream evolves.

Cross-refs from a workstream page to:
- The owner page(s)
- Recent status pages
- Related decision pages
- Concept pages relevant to the workstream

## When to create concept pages

ONLY when the source introduces a NEW concept with no existing home. New
concept pages are the rarest of the seven kinds — most overlap is handled
by updates + new entity / workstream / decision pages, not new concepts.

## When to refuse — RARELY

The `low-quality` and `out-of-scope` verdicts are reserved for sources that
genuinely contain no load-bearing content for THIS initiative. Topical overlap
with an existing page is NOT grounds for refusal — it warrants UPDATES + NEW
ENTITY / DECISION / STATUS PAGES.

Refuse only when:
- The source has zero factual claims any wiki page could cite (e.g. a utility
  script with no novel logic for the initiative)
- The source's subject matter is wholly outside the initiative's domain

## Cross-references — REQUIRED ON CREATION

Every new page MUST include at least one cross-reference to a related existing
page. A new page with no inbound or outbound links is an orphan and a lint
failure.

For initiative wikis, the most common cross-ref patterns are:
- `decision` → `workstream`, `owner`, `concept`
- `status` → `workstream`, `owner`
- `workstream` → `owner`, recent `status`, related `decision`, related `concept`
- `entity` → `workstream` (if the entity participates), `concept` (if the entity exemplifies)

## Synthesis pages — TRIGGER CONDITIONS

Propose a `synthesis` page when:
- A new source ties together ≥3 existing decision / workstream / status pages, OR
- You notice ≥3 existing pages would benefit from a unified writeup that
  compares/contrasts them (e.g. "every funnel-status page over Q1 → Q2
  trajectory synthesis")

Synthesis pages are short cross-cutting essays (300–800 words) with heavy
cross-reference density.

## Language

Write page prose in the language of the source. Keep YAML frontmatter keys, `kind` values, file names, and section headings in English so tooling and cross-language corpora keep working.

## Lint policies

- Pages with no inbound `cross_refs` are flagged as orphans
- Pages whose source files were modified after `last_touched_at` are flagged as stale
- Pages with header depth > 4 are flagged for restructuring
- Sources with backref count of 0 after ingest are flagged as poorly absorbed
- Broken cross-references (markdown link to a path that doesn't exist) are flagged
- Workstream pages with no recent status (>90 days) are flagged
- Decision pages without a `date` in frontmatter are flagged
- Status pages that are edited after creation are flagged (status is immutable)

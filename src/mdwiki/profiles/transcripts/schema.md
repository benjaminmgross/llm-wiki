# mdwiki schema — `transcripts` profile

The conventions and policies the LLM follows when ingesting **meeting / call /
interview transcripts** — typically VTT, SRT, or markdown exports from Fathom
/ Otter / Zoom. The corpus is dense, low-signal-density text with speaker
turns, often time-coded.

This profile is opt-in: `mdwiki init --profile=transcripts`. Use it when the
folder is *primarily* transcripts. For initiative folders that contain a
`transcripts/` subdir alongside other materials, use the `initiative` profile
at the top level — the `TranscriptLoader` auto-handles transcript files there
based on extension.

## Page kinds

| Kind | Folder | Purpose |
|---|---|---|
| `entity` | `wiki/entities/` | A specific person, organization, project, or system referenced across transcripts. Speakers themselves get `person` entity pages. |
| `meeting` | `wiki/meetings/` | One page per meeting/call. Captures date, participants, topics covered, and outcomes. |
| `decision` | `wiki/decisions/` | A specific decision made during a transcript: the choice itself, the alternatives considered, the speakers involved, the date. |
| `commitment` | `wiki/commitments/` | A specific commitment: who committed, what they committed to, when (deadline), and the cross-ref back to the meeting where it was made. |
| `blocker` | `wiki/blockers/` | A specific impediment: who raised it, what's blocked, what's needed to unblock, cross-ref to relevant meeting. |
| `concept` | `wiki/concepts/` | A concept, theme, or idea recurring across multiple transcripts. |
| `synthesis` | `wiki/syntheses/` | A cross-cutting writeup that braids multiple meetings together (e.g. "trajectory of Q2 fundraise discussions"). |

## Speaker → person entity is MANDATORY

For every named speaker in a transcript, REQUIRED: create or update a `person`
entity page. The page captures:

- Their role / affiliation (best inferred from the source)
- Recurring topics they speak to
- Cross-refs to the meetings they appeared in (back-links)
- Cross-refs to commitments they made and decisions they participated in

Without person entity pages, transcripts collapse into a wall of meeting
summaries with no cross-meeting tracking. Person pages are how the wiki turns
"who said what across N meetings" into a queryable graph.

## Naming

- Person entities: lowercase-kebab-case, full name where known: `sarah-chen.md`, `bob-from-acme.md`
- Meetings: ISO date prefix + slug — `2026-04-30-q2-investor-update.md`
- Decisions: ISO date prefix — `2026-04-30-pivot-to-property-management.md`
- Commitments: ISO date prefix + person + slug — `2026-04-30-sarah-ship-by-friday.md`
- Blockers: ISO date prefix + slug — `2026-04-30-vendor-contract-stalled.md`
- Concepts and syntheses: noun phrases — `term-sheet-negotiation.md`

## Citation format with timestamps

Transcript citations include the speaker and timestamp where available:

```
[^src1]: <quote>... (Sarah, 00:12:34, raw/<hash>-<slug>.md, original: <original_path>)
```

Quote-anchor verification runs in `transcripts` mode for sources from this
profile: `[HH:MM:SS]` timestamps and `Speaker:` line prefixes are stripped
before grep, so an extracted quote that starts mid-utterance still anchors.

## Touch breadth target

**8–20 wiki pages per transcript ingest** — typically:

- 1 NEW meeting page (one per source)
- 2–6 NEW or UPDATED person entities (one per named speaker)
- 0–4 NEW decision pages (one per decision actually made — not every "we should...")
- 0–6 NEW commitment pages (one per concrete "I'll do X by Y")
- 0–3 NEW blocker pages
- 6–15 cross-references

Plans touching ≤4 pages on a 30+ minute transcript are almost always
under-creating; the source has many named subjects and concrete
decisions/commitments worth pages.

## When to create decision vs. commitment vs. blocker pages

The distinction matters — these three page kinds capture transcript outcomes:

- **Decision**: a *choice* between alternatives was made. Test: was there a
  real "we considered X and Y, and we chose X" moment?
- **Commitment**: a *promise* to do something specific by someone specific.
  Test: did a named person commit to a named action with a deadline (even
  vague — "next week")? Speculative future plans aren't commitments.
- **Blocker**: an *impediment* that's stalling work, with named ownership
  of the unblocking action. Test: is something explicitly stuck pending
  someone else's action?

Vague "we should think about ..." doesn't deserve a decision/commitment/
blocker page. Capture it in the meeting page as a topic discussed.

## When to refuse — RARELY

The `low-quality` and `out-of-scope` verdicts are reserved for transcripts
that genuinely contain no load-bearing content: e.g. a transcript that's
99% small-talk, or a recording of a different team's meeting that ended up
in this folder by accident.

Do NOT refuse a transcript just because:
- It overlaps topically with prior transcripts (overlap is the common case;
  it warrants UPDATES + new commitment / decision pages)
- It's long and dense (length is expected; chunker fallback tiers handle this)
- The speakers are already in the wiki (UPDATE their person pages)

## Cross-references — REQUIRED ON CREATION

Every new page MUST link to at least one other page. For transcripts:

- Meeting pages must cross-ref every person entity for a speaker who attended
- Decision / commitment / blocker pages must cross-ref the meeting page they
  came from AND the person entity for the named actor
- Person entity pages should cross-ref the meetings they participated in

A new page with no inbound or outbound links is an orphan.

## Synthesis pages — TRIGGER CONDITIONS

Propose a `synthesis` page when:

- A new meeting ties together themes from ≥3 prior meeting pages, OR
- ≥3 commitment pages from the same person trace a coherent trajectory, OR
- A topic recurs in ≥3 meetings and would benefit from a unified writeup

Synthesis pages are short cross-cutting essays (300–800 words). They are
NOT meeting summaries.

## Language

Write page prose in the language of the source. Keep YAML frontmatter keys, `kind` values, file names, and section headings in English so tooling and cross-language corpora keep working.

## Lint policies

Transcripts-specific lint rules (kind-aware checks for `meeting`, `decision`,
`commitment`, `person`) are deferred to a follow-up phase. For now, only the
generic rules ship and run against transcripts wikis:

- Pages with no inbound `cross_refs` are flagged as orphans (catches orphan
  meetings, orphan person entities, orphan decisions)
- Pages whose source files were modified after `last_touched_at` are flagged as stale
- Broken cross-references (markdown link to a path that doesn't exist) are flagged
- Sources marked `ingested` but with zero backrefs are flagged as `coverage-gap`
- Pages containing the `[unverified-quote]` marker are flagged for review

When transcripts-aware lint lands:
- Meeting pages without a `meeting_date:` in frontmatter → `meeting_missing_date`
- Commitment pages without a named owner cross-ref → `commitment_missing_owner`
- Decision pages without alternatives discussed → `decision_missing_alternatives`
- Person entities with 0 meeting cross-refs → `orphan_speaker`

# mdwiki schema — `framework` profile

The conventions and policies the LLM follows when ingesting **frameworks** —
folders that document a procedure or methodology, with templates / rubrics /
phases as reusable artifacts, applied assessments as instances, and learnings
as cross-instance patterns. Examples: testing-framework, bugfix-framework,
harness-framework — anything fitting the procedure → template → assessment →
learning loop.

This profile is opt-in: `mdwiki init --profile=framework`. Use it when the
folder *primarily* documents a framework. Mixed initiative folders that
contain a framework as one workstream should use the `initiative` profile
at the top level.

## Page kinds — the four-kind taxonomy

| Kind | Folder | Purpose |
|---|---|---|
| `procedure` | `wiki/procedures/` | The canonical "how the framework works" — README + protocol. One per framework. |
| `template` | `wiki/templates/` | A reusable artifact: scoring rubric, evaluation template, phase definition, pattern. Templates are versioned. |
| `assessment` | `wiki/assessments/` | A single application of a template to a specific situation. Immutable once recorded. Cross-refs the template it instantiates. |
| `learning` | `wiki/learnings/` | A cross-instance pattern observed across ≥2 assessments. Cross-refs the assessments that evidence it. |
| `entity` | `wiki/entities/` | A specific person, team, system, or product the framework references. |
| `concept` | `wiki/concepts/` | A reusable idea or pattern beyond what fits in a template. |
| `synthesis` | `wiki/syntheses/` | A cross-cutting writeup that braids multiple learnings into a strategic recommendation. |

## The procedure → template → assessment → learning loop

This is *the entire reason a framework profile exists*. The loop:

1. **Procedure** describes how to apply the framework
2. **Templates** are the reusable artifacts the procedure invokes (rubrics, phases, evaluation forms)
3. **Assessments** are instances of templates applied to real situations
4. **Learnings** observe patterns across multiple assessments and propose updates back to templates

A framework wiki's value compounds when this loop is closed. Templates without
applied assessments are unused; assessments without learnings are silent;
learnings that don't refine templates are dead-ends.

## Naming

- Procedures: noun phrase — `testing-framework.md` (lives at `procedures/`)
- Templates: kebab-case noun phrase — `evaluation-template.md`, `scoring-rubric.md`, `phase-1-discovery.md`
- Assessments: ISO date prefix + situation — `2026-04-30-minty-living-assessment.md`
- Learnings: short noun phrase — `recurring-pattern-of-flaky-integration-tests.md`
- Entities and concepts: kebab-case noun phrase

## Touch breadth target

**5–15 wiki pages per source ingest** — typically:

- 0–1 NEW or UPDATED procedure (rare; the procedure is stable)
- 0–3 NEW or UPDATED templates
- 0–2 NEW assessments (assessments are typically created when a real
  situation is being recorded, not when reading framework docs)
- 1–4 NEW or UPDATED learnings (learnings accumulate as the framework matures)
- 4–10 cross-references

## When to create each kind

- **Procedure**: only when ingesting the framework's README or protocol doc.
  ONE procedure page per framework.
- **Template**: when the source describes a reusable artifact (a rubric, a
  scoring template, a phase definition, a pattern). New templates rare;
  template updates common.
- **Assessment**: when the source records a *specific instance* of the
  framework being applied — e.g. `assessment-2026-04-24-minty-living.md`.
  Assessments should be IMMUTABLE: once recorded, they're a historical
  artifact. Edits go in a NEW assessment with `previous_hash:` linking back.
- **Learning**: when a source observes a pattern that emerged from multiple
  assessments. Learnings cite the assessments that evidence them.

## Page version chain (`previous_hash:`)

Every page in this wiki carries a `previous_hash:` in its YAML frontmatter
linking back to the previous version's body hash. For the framework profile,
this matters most for:

- **Templates** (which evolve over time — new versions supersede old, but the
  chain preserves the evolution)
- **Procedures** (rare edits, but each one is meaningful)
- **Assessments** (immutable in spirit; the hash chain proves they haven't
  been retroactively edited)

## When to refuse — RARELY

The `low-quality` and `out-of-scope` verdicts are reserved for sources that
genuinely contain no load-bearing content for THIS framework: e.g. a doc
that's about a different framework that ended up in this folder by accident.

Do NOT refuse when:
- The source is short — many framework artifacts are intentionally short
- The source overlaps with an existing template — most updates do; that's
  the template-evolution case

## Cross-references — REQUIRED ON CREATION (DAG)

The framework profile enforces a DAG structure:

- `procedure` → `template`s the procedure invokes
- `template` → none required outbound; should have INBOUND refs from assessments
- `assessment` → exactly one `template` (the one it instantiates), plus may
  ref entities + concepts
- `learning` → ≥1 `assessment` (the evidence) + may propose updates to a `template`

Lint rules (see below) will surface violations of this DAG.

## Synthesis pages — TRIGGER CONDITIONS

Propose a `synthesis` when:

- ≥3 learnings on the same theme suggest a strategic recommendation, OR
- The framework's procedure should change based on accumulated learnings, OR
- A pattern emerges that warrants a position statement beyond a learning

## Language

Write page prose in the language of the source. Keep YAML frontmatter keys, `kind` values, file names, and section headings in English so tooling and cross-language corpora keep working.

## Lint policies

Strict DAG enforcement is deferred to a follow-up phase (requires dropping
the existing `pages.kind` CHECK constraint). For now:

- Pages with no inbound `cross_refs` are flagged as orphans (catches orphan
  templates, orphan assessments, orphan learnings)
- Pages whose source files were modified after `last_touched_at` are flagged as stale
- Pages with header depth > 4 are flagged for restructuring
- Sources with backref count of 0 after ingest are flagged as poorly absorbed
- Broken cross-references (markdown link to a path that doesn't exist) are flagged

When DAG-aware lint lands:
- `template` pages with 0 assessments referencing them → `orphan_template`
- `assessment` pages without exactly one template cross-ref → `assessment_missing_template`
- `learning` pages without ≥1 assessment cross-ref → `learning_missing_evidence`

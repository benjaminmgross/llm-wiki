# llm-wiki Roadmap

This roadmap preserves an earlier comparative assessment and proposed work.
Historical assessment notice (2026-10-07): the original comparison date,
repository revisions, and supporting evaluation artifacts were not recorded
here. External repository descriptions and scores below are unverified
historical judgments, not current benchmarks or independently validated
rankings. They were not re-researched for the session-provider update.

## Current Implementation Corrections (2026-10-07)

- Session CLI support, configured-provider vision registration, and
  OpenAI-compatible environment key lookup are included in the pending
  [unreleased changes](RELEASES.md). Native `session-ingest` remains available
  for active-session planning with serialized parent apply.
- Batch preference already honors provider capabilities and falls back through
  the same configured provider.
- Planned cross-references already become relative Markdown links in page
  bodies; typed relationship storage and graph export remain proposals.
- Refusal-aware coverage lint is implemented: valid `low-quality`,
  `out-of-scope`, and `duplicate-of:<page>` verdicts in the latest durable
  rejection or ingest event suppress coverage gaps. Dedicated source
  disposition fields and richer reports remain proposals.

The remaining roadmap and historical comparison should be read with these
corrections. [README.md](README.md) documents the current command behavior.

## Repositories Reviewed

| Label | Repository | Language | Version |
| --- | --- | --- | --- |
| A | `benjaminmgross/llm-wiki` (`mdwiki`) | Python | v1.3.0 |
| B | `oadank/openclaw-wiki-lancedb` | JavaScript (Node) | initial commit |
| C | `doum1004/llmwiki-cli` | TypeScript (Node) | v1.0.1 |
| D | `skyllwt/OmegaWiki` | Python + Claude Code skills | v1.3.0 |

## Evaluation Matrix

Scale: 1 (weak) to 5 (exemplary).

| Dimension | A: llm-wiki | B: openclaw | C: llmwiki-cli | D: OmegaWiki |
| --- | ---: | ---: | ---: | ---: |
| Code Quality | 5 | 2 | 4 | 4 |
| Package Structure | 5 | 2 | 4 | 4 |
| Package Functionality | 4 | 2 | 3 | 5 |
| Algorithm Implementation Quality | 5 | 2 | 2 | 4 |
| Robustness and Error Handling | 5 | 2 | 3 | 3 |
| Algorithm Sophistication | 4 | 2 | 1 | 4 |
| Token Efficiency | 4 | 2 | 1 | 3 |
| Hallucination Guardrails | 5 | 1 | 1 | 3 |
| Schema / Knowledge Modeling | 4 | 2 | 2 | 5 |
| Observability / Auditability | 5 | 2 | 3 | 3 |
| Extensibility / Plugin Model | 4 | 2 | 3 | 4 |
| Onboarding / DX | 4 | 3 | 4 | 3 |
| Test Coverage | 3 | 1 | 4 | 2 |
| Maintenance Maturity | 4 | 1 | 3 | 4 |
| **Total (/70)** | **61** | **26** | **38** | **51** |

## Summary

`llm-wiki` is the strongest implementation for engineering rigor. Its best-in-class
areas are hallucination guardrails, transactional safety, token budgeting, package
architecture, auditability, and deterministic ingest validation.

The biggest competitive gaps are not in core reliability. They are in higher-order
knowledge operations: typed relationships, external discovery, graph visualization,
structured synthesis lifecycles, and richer progress feedback for long-running work.

## Strengths Identified in the Historical Assessment

### Engineering Rigor

`llm-wiki` is a proper Python package using a `src/mdwiki/` layout, typed
dataclasses, a `py.typed` marker, clear module boundaries, and focused modules for
ingest, chunking, quote verification, transaction handling, state, linting, and
provider integration.

The ingest pipeline is the strongest part of the system:

- Tiered chunking with graceful fallback: H2, H1, paragraph, then sliding window.
- Local candidate retrieval with sentence-transformers embeddings and cosine
  similarity.
- Quote verification in pure Python before applying LLM-generated citations.
- Retry logic for invalid plans.
- SQLite-backed transactions with undo snapshots.
- Tamper-evident page version chains using `previous_hash`.
- Content-hash deduplication on refresh.

### Hallucination Guardrails

Every LLM-generated citation is verified against the raw source before being
applied. The schema gives the model explicit permission to refuse low-quality,
out-of-scope, or duplicate sources. Pages can carry an `[unverified-quote]` marker
that `mdwiki lint` flags for review.

This makes `llm-wiki` meaningfully safer than systems that apply LLM output
directly.

### Observability and Auditability

`llm-wiki` has strong operational primitives:

- Append-only `wiki/log.md`.
- SQLite state for sources, pages, events, rejections, and cost ledger entries.
- Per-transaction undo snapshots.
- `mdwiki status` for counts and recent events.
- `mdwiki source <hash>` for source inspection.
- `mdwiki rebuild-log` for recovery.
- Page-level `previous_hash` chains.

### Token and Cost Efficiency

The project already uses several token-saving mechanisms:

- Candidate retrieval sends only relevant pages to the LLM.
- Quote banks are bounded.
- Batch ingestion can use Anthropic Batch API for lower cost.
- `cost_guard` supports daily budget enforcement.
- `candidate_top_k` is configurable.
- The model can refuse low-value sources.
- Retry prompts summarize failed plan context rather than replaying everything.

## Comparative Gaps

### 1. Knowledge Graph and Typed Relationships

The earlier assessment described OmegaWiki as having typed semantic edges, a
formal YAML schema, and a separate citation graph. Current `llm-wiki`
materializes cross-references as relative Markdown links inside page bodies;
it does not yet export a typed relationship layer.

Adding a lightweight graph layer would compound the value of the wiki. Candidate
edge categories:

- `builds_on`
- `contradicts`
- `implements`
- `summarizes`
- `supersedes`
- `depends_on`
- `owned_by`
- `evaluates`
- `mentions`

### 2. External Discovery

OmegaWiki includes discovery workflows such as venue/year paper ranking, fresh
paper recommendations, and Semantic Scholar integration. `llm-wiki` has no
equivalent.

This matters most for users working in evolving domains where the wiki should not
only preserve known sources, but also recommend what to ingest next.

### 3. Knowledge Graph Visualization

OmegaWiki and `llmwiki-cli` both offer visual exploration layers. `llm-wiki` does
not currently expose graph visualization or visual navigation over pages, sources,
and relationships.

### 4. Test Coverage and Regression Strategy

`llm-wiki` has an existing automated test suite and a structural canary runner.
The gap is less "no tests" and more that the highest-risk behaviors should remain
heavily covered as the system grows:

- Ingest plan validation and retry behavior.
- Quote verification edge cases.
- Transaction rollback and undo behavior.
- Lint and lint-fix behavior.
- Provider failure modes.
- Real-folder canary regression trends.

### 5. Idea and Synthesis Lifecycle

OmegaWiki treats the wiki as a research platform with a lifecycle across ideation,
novelty checking, experiment design, experiment evaluation, paper drafting, and
review.

`llm-wiki` has `synthesize`, but synthesis outputs do not yet have a structured
lifecycle, validity score, maturity state, or recommended follow-on actions.

### 6. Streaming and Progress Feedback

Long-running commands such as bootstrap, batch ingest, and full lint repair would
benefit from richer progress feedback. Planned `--verbose` support should become a
first-class operational improvement rather than a nice-to-have.

## Roadmap Priorities

### P0: Preserve the Core Reliability Advantage

These are non-negotiable foundations that should remain strong while new features
are added.

- Keep quote verification fail-closed by default.
- Keep ingest writes transactional and undoable.
- Maintain deterministic lint behavior for non-LLM fixes.
- Expand regression tests around recently fixed ingest retries and lint bounds.
- Keep provider-specific logic isolated behind clean provider interfaces.

### P1: Add a Lightweight Typed Graph

Introduce a machine-readable relationship layer without replacing markdown pages.

Proposed deliverables:

- `wiki/graph/edges.jsonl` with typed edges between pages, sources, and entities.
- `wiki/graph/citations.jsonl` or a DB-backed equivalent for citation metadata.
- A small initial edge ontology.
- `mdwiki graph export` to emit graph data.
- `mdwiki lint` checks for invalid edge targets and unsupported edge types.
- Ingest-plan support for proposed typed edges.

Design constraint: typed graph data should complement wikilinks, not require users
to abandon readable markdown pages.

### P2: Improve Lint Semantics for Real-World Wikis

Refusal-aware coverage lint is already implemented. Further work can expose
source dispositions more directly and improve reporting.

Proposed deliverables:

- Completed: exclude valid intentional refusals from coverage-gap findings
  using the latest durable rejection or ingest event.
- Add source disposition metadata to the state DB and sidecar.
- Add `mdwiki lint --json` for machine-readable reporting.
- Add grouped lint summaries by folder, file type, and source disposition.
- Add orphan triage helpers that suggest likely parent pages without applying
  editorial changes automatically.

### P3: External Discovery and Ingest Recommendations

Add discovery workflows for domains that change over time.

Proposed deliverables:

- `mdwiki discover` command with pluggable discovery providers.
- Initial provider for Semantic Scholar or arXiv.
- A "recommended sources" queue that can be reviewed before ingest.
- Source novelty scoring against the existing wiki.
- Budget-aware discovery limits.

### P4: Visualization and Navigation

Add a visual exploration layer for page, source, and edge graphs.

Proposed deliverables:

- `mdwiki graph export --format json`.
- Static HTML graph viewer.
- Optional Obsidian Canvas export.
- Graph metrics in `mdwiki status`, such as isolated pages, dense clusters, and
  central entities.

### P5: Synthesis Lifecycle

Make synthesis outputs more durable and actionable.

Proposed deliverables:

- Frontmatter fields for synthesis lifecycle state.
- Maturity states such as `draft`, `validated`, `superseded`, and `archived`.
- Follow-on action suggestions.
- Validity or evidence-strength scoring.
- `mdwiki synthesize --review` to re-check old syntheses against newer sources.

### P6: Better Progress Feedback

Improve CLI ergonomics for long-running operations.

Proposed deliverables:

- `--verbose` support for ingest, refresh, bootstrap, and lint fix.
- Progress counters for pending sources.
- Provider latency and token/cost reporting.
- Clear retry messages when ingest plans are invalid.
- Better summaries for batch submission, polling, and completion.

## Dimension-by-Dimension Notes

### Code Quality

`llm-wiki` scores highest due to its proper Python packaging, typed data models,
module boundaries, constants for algorithm tuning, and disciplined separation of
concerns.

The main risk is not current code quality. The risk is that feature expansion could
pull graph, discovery, and lifecycle concepts into large orchestration modules. New
features should preserve the existing modular style.

### Package Structure

The existing `src/` layout, `pyproject.toml`, profile system, loader modules, and
provider abstraction are strong. Future graph and discovery work should use new
dedicated modules rather than expanding `ingest.py` or `cli.py` excessively.

### Package Functionality

The core lifecycle is strong:

```text
init -> refresh -> ingest -> query -> synthesize -> lint -> undo
```

The functional gap is above the core lifecycle: discovery, graph navigation,
visualization, and synthesis lifecycle management.

### Algorithm Quality

The strongest algorithms today are chunking fallback, retrieval-bounded context,
quote verification, plan retry, transactional apply, and version chaining.

The next algorithmic step is typed graph extraction and graph-aware retrieval.

### Robustness and Error Handling

Invalid-plan retries, bounded lint fixes, and refusal-aware coverage checks are
implemented. Further work can make source dispositions explicit in the state
schema and reporting without changing the existing refusal classification.

### Token Efficiency

Candidate retrieval, bounded quote banks, batch API support, and budget tracking
are already good. Future work should avoid adding discovery and graph operations
that blindly expand prompt context.

### Hallucination Guardrails

This is the project's clearest advantage. New graph and discovery features should
inherit the same fail-closed posture: no edge, citation, or recommendation should
be persisted without traceable evidence.

### Schema and Knowledge Modeling

The markdown schemas are flexible and agent-friendly. The gap is machine-readable
schema enforcement for relationships and lifecycle states.

### Observability and Auditability

The event log, state DB, source inspection, undo, and version chain are strong.
Graph and discovery work should extend the same audit model, not create opaque
side effects.

### Onboarding and DX

The project already has strong CLI discoverability. The main DX improvement is
better progress feedback during long-running operations and richer lint summaries
for large real-world wikis.

## Near-Term Candidate Issues

1. Add source disposition tracking for duplicate, low-quality, and out-of-scope
   ingests.
2. Completed: ignore valid intentional refusals in `coverage-gap` lint;
   separate disposition reporting remains a candidate.
3. Add `mdwiki lint --json`.
4. Add graph edge model and storage format.
5. Add graph export command.
6. Add orphan parent-page suggestion command.
7. Add verbose progress reporting for ingest and lint fix.
8. Add regression tests around source disposition and lint classification.

## Success Criteria

The roadmap is working if future `llm-wiki` releases can:

- Preserve quote-verified, transactional ingest.
- Explain source coverage without noisy false positives.
- Export a typed relationship graph from normal wiki pages.
- Support visual graph exploration.
- Recommend new sources to ingest for evolving domains.
- Track synthesis outputs through a clear lifecycle.
- Give operators useful progress and cost feedback during long runs.

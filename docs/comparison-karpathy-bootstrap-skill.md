# Gap analysis: mdwiki vs `nanzhipro/Karpathy-llm-wiki-bootstrap-skill`

Date: 2026-09-02. Compared against the reference repo at its default branch on that date.

## What each repo is

| | mdwiki (this repo) | Karpathy-llm-wiki-bootstrap-skill |
|---|---|---|
| Shape | Python CLI (`mdwiki`), ~60 test modules, sqlite state, local embeddings | Prompt-only skill: ~3.8k lines of Markdown plus one stdlib Python script (`wiki_fts.py`) |
| Install | `uv sync`, run `.venv/bin/mdwiki` | `npx skills add nanzhipro/Karpathy-llm-wiki-bootstrap-skill@llm-wiki-bootstrap` |
| Who does the work | The CLI calls a provider, verifies output, applies transactionally | The agent reading the skill does everything by hand, following workflow docs |
| Retrieval | Dense embeddings (sentence-transformers) over page bodies | Optional SQLite FTS5 / BM25 over chunks |
| Verification | Cite-or-refuse, verbatim quote anchoring, schema-validated JSON, corrective retries | None (relies on the agent) |
| Safety | Per-ingest sqlite transaction, undo snapshots, write lock, version chain | None beyond "never write to raw/" |
| Inputs | md, txt, code, csv, pdf, docx, html, images, vtt/srt transcripts | Whatever the agent can read |
| Scale | Batch API, `refresh --bootstrap` for cron, native-session multi-agent ingest | Sequential, interactive |

mdwiki is the stronger system by a wide margin as infrastructure. The reference repo is stronger in one narrow area: it is packaged and documented as an agent-facing skill with clear per-operation workflows, and its generated wiki has richer navigation and hygiene conventions. Those are the gaps worth closing.

## What the reference has that mdwiki lacks

Ranked by value relative to effort.

### P0. The `skill/` directory is stale and describes a product that no longer exists

`skill/SKILL.md` is still the `markdown-consolidator` skill. It tells an agent to run `scripts/inventory.py`, `cluster.py`, `plan_merge.py`, `synthesize.py`, `validate.py`, and `consolidate.py`. None of those files exist; `scripts/` contains only `run_canary.py`. `skill/references/{ALGORITHMS,CONFLICT-RESOLUTION,INTEGRATION}.md` and `CONTRIBUTING.md` are also consolidator-era. The legacy modules `consolidator.py`, `clustering.py`, `inventory.py`, `tree_builder.py`, `synthesis.py`, `relationships.py`, `keywords.py`, and `summarizer.py` are not imported by anything in `src/` (only their tests keep them alive).

The reference repo's whole value proposition is a short `SKILL.md` with an intent router and progressive-disclosure references. mdwiki has the seed of this in `mdwiki skill` (the runtime print command) but nothing installable.

Recommendation:

- Rewrite `skill/SKILL.md` as an `mdwiki` skill: an intent table (bootstrap, ingest, refresh, query, synthesize, lint, undo, session-ingest) that maps each intent to the CLI command and to one reference file.
- Move the `AGENT_GUIDE` text out of `skill.py` into `skill/references/workflows/*.md` and have `mdwiki skill` print those files, so the runtime guide and the installable skill are the same source.
- Delete or archive the consolidator references and the dead modules, and update `CONTRIBUTING.md` and the `pyproject.toml` URLs (they point at `bgross/mdwiki`).
- Make the skill installable with the `skills` CLI the reference uses, so a new machine gets the workflows without cloning the repo.

### P0. Runtime pointer files at init

The reference writes thin `CLAUDE.md`, `AGENTS.md`, and `.github/copilot-instructions.md` files into the wiki root that redirect to `SCHEMA.md`. An agent that opens the folder finds its instructions automatically. `mdwiki init` writes only `.mdwiki/schema.md`, so an agent has to already know to run `mdwiki skill`.

Recommendation: add `--pointers=claude,codex,copilot` to `init` (default on for at least `CLAUDE.md` and `AGENTS.md`). The pointer is five lines: identity, "run `mdwiki skill` and read `.mdwiki/schema.md` before any operation", never write `raw/`, and the three trigger phrases. Keep operating rules out of the pointers, as the reference does.

### P0. A no-LLM lexical search command for agents

The reference ships `wiki_fts.py` (SQLite FTS5, stdlib only) and requires the agent to search before creating a page, to avoid duplicates. mdwiki has dense embeddings, which are better for semantic questions but miss exact names, dates, and identifiers, and they require the sentence-transformers dependency.

The sharper problem is the native-session path. `session-ingest prepare` deliberately avoids the embedder, so a sub-agent planning a source has no candidate finder at all beyond `index.md` and grep. That is exactly the situation the reference's BM25 layer exists for.

Recommendation: add an FTS5 virtual table over page chunks inside `state.db` (rebuilt in `rebuild --pages` and after every transaction) and a `mdwiki search "<terms>" [--limit N]` command that prints page path, heading path, and a snippet. No new dependency; FTS5 is already compiled into the bundled sqlite. Then reference it from the session-ingest envelope instructions and from the skill's ingest workflow. Later, use it alongside embeddings for hybrid candidate selection in `ingest` and `query`.

### P1. Contradiction handling end to end

The reference's ingest step writes an explicit contradiction block into both pages when a new source disagrees with existing content, and its lint checks for unresolved contradictions and for stale claims superseded by a newer source. `lint.py`'s docstring mentions contradiction detection, but the shipped rules are broken refs, orphans, stale-by-mtime, coverage gaps, and unverified quotes. The plan JSON has no contradiction field.

Recommendation:

- Add `contradictions: [{page, existing_claim, source_claim, resolution}]` to the plan contract, with the same quote-anchor requirement as claims.
- Materialize them as a managed `<!-- mdwiki:contradictions -->` block, the same way cross-refs are materialized.
- Add lint rule `unresolved-contradiction` (resolution still `pending` after N later ingests) and surface counts in `status`.

### P1. Navigation pages beyond `index.md`

The reference maintains three navigation surfaces: `index.md` (catalog), `overview.md` (top-level synthesis revised on every ingest that changes the big picture), and `concept-table.md` (one row per concept: working definition, role, sources, related pages, status such as `single-source` or `contradicted`, and a maintenance note). mdwiki has only the auto-generated `index.md`.

Recommendation:

- `concept-table.md` can be generated deterministically from `state.db` with no LLM call: sources per page from backrefs, status from distinct-source count and contradiction state, related pages from cross-refs. Regenerate it wherever `regenerate_index` runs.
- `overview.md` needs an LLM. Add `mdwiki overview` that reuses the synthesize prompt with the index and concept table as input, and offer `--overview` on `ingest --pending` and `refresh --bootstrap` to refresh it once at the end of a batch.

### P1. Per-source summary pages and page frontmatter

The reference creates `wiki/sources/{slug}.md` for every ingested source (summary, key claims with quotes, entities, concepts, notable quotes, limitations and bias) and puts `title`, `type`, `created`, `updated`, `sources`, and `tags` in every page's YAML frontmatter. mdwiki keeps provenance in `raw/.sources.json` and `state.db` (readable via `mdwiki source <hash>`), with footnote citations in page bodies, and writes only `previous_hash` to frontmatter.

Recommendation: add a `source` page kind to the default schema (one page per ingested source, generated from the plan's verdict, rationale, and claims) and write `sources`, `tags`, and `updated` frontmatter keys on every page write. The version-chain code already owns frontmatter, so the plumbing exists. This makes wikis usable from Obsidian and Dataview, which the reference targets explicitly.

### P1. Lint: duplicate detection, richer checks, triage UX

The reference's lint covers more categories: missing pages (a term mentioned three or more times without a page), duplicate candidates, missing backlinks, isolated clusters, tag inconsistency, empty pages, and an outdated overview. It groups the report by severity, asks "which items should I fix" (all, high only, or a list), and prints a suggestions section.

mdwiki already stores `severity` on every finding but prints findings grouped by kind. It also already has page embeddings, which make near-duplicate detection nearly free.

Recommendation:

- Add `duplicate-candidate` (pairwise cosine above a threshold between pages of the same kind) and `isolated-cluster` (connected components of the cross-ref graph) rules.
- Group the report by severity and accept a `--fix=1,3,7` selection.

### P2. Research and media profiles

The reference injects domain page types for four domains: research (paper, claim, method, dataset), book or media (character, timeline, plot thread, theme, location), personal (journal, goal, habit, lesson), and business (decision log, meeting, project, stakeholder). mdwiki's profiles cover business-shaped corpora well (`initiative`, `transcripts`, `framework`) and have nothing for research or reading corpora, even though the README's calibration corpus is a folder of web clippings.

Recommendation: add a `research` profile (paper, claim, method, dataset page kinds). A profile is a `schema.md` plus a `config-overlay.toml`, so this is mostly writing.

### P2. Query workflow refinements

Small prompt and CLI changes the reference specifies that mdwiki does not:

- State a confidence level per answer (high: multiple corroborating pages, medium: single page, low: inference).
- Offer to file the answer when it is file-worthy (comparison, new connection, synthesis across three or more pages). mdwiki has `--file` but never suggests it.
- When the wiki cannot answer, optionally create a stub page tagged `stub, needs-sources` so the gap is visible in lint.
- Log filed answers as a distinct `query-filed` event.

### P2. Language preservation rule

The reference requires wiki pages to be written in the source's language while keeping YAML keys, type values, filenames, and headings in English. mdwiki's schema and prompts say nothing about language. One paragraph in `DEFAULT_SCHEMA` and the ingest system prompt closes this.

### P2. User-level preferences

The reference reads an `EXTEND.md` from the project, then XDG config, then the home directory. mdwiki configuration is per-wiki only (`.mdwiki/config.toml`). Anyone running several wikis repeats the provider block, and the `api_key` warning in the README exists partly because there is no user-level place for it.

Recommendation: read `${XDG_CONFIG_HOME:-~/.config}/mdwiki/config.toml` as a base layer under the wiki's `config.toml`, using the existing `deep_merge`.

### P2. A living example wiki in the repo

The reference ships `llm-wiki/`, a real wiki built from Karpathy's gist, and points readers at it as the fastest way to understand the system. mdwiki's `examples/sample-docs/` is three consolidator-era auth notes with no generated wiki. Running the canary on a small public corpus and committing the output would serve both as documentation and as a lint fixture.

### Low. Editor integration

The reference appends an Obsidian setup section to the schema (attachment folder, Dataview, graph view) and can write `.vscode/settings.json`. mdwiki's relative Markdown links already work in Obsidian. Worth a short README section, not code.

## What mdwiki has that the reference lacks

Do not regress these while closing the gaps above. The reference has none of them.

- Verbatim quote anchoring, cite-or-refuse, schema-validated plans with bounded corrective retries
- Per-ingest transactions, `undo`, cross-process write lock, page version chain
- Loaders for PDF, DOCX, HTML, CSV, code, images, and VTT/SRT transcripts
- Content-hash dedup, `refresh` for cron, durable failed-source reasons in `status`
- Anthropic Batch API bootstrap and an OpenAI-compatible provider seam
- Native-session multi-agent ingest with optimistic page hashes
- Corpus-aware profiles with tuned chunking and quote-anchor modes
- Cost ledger and budget guard, rejection memory
- A real test suite and a canary scorecard

## Suggested order of work

1. Rewrite `skill/` as the `mdwiki` skill and remove the consolidator remnants (P0, mostly writing).
2. Pointer files at `init` (P0, small).
3. FTS5-backed `mdwiki search` and its use in the session-ingest envelope (P0, medium).
4. Contradiction field in the plan, managed block, lint rule (P1, medium).
5. Deterministic `concept-table.md`, then `mdwiki overview` (P1, small then medium).
6. `source` page kind and frontmatter keys (P1, medium).
7. Duplicate and isolated-cluster lint rules, severity-grouped report with selectable fixes (P1, small).
8. `research` profile, language rule, query confidence and filing prompt, user-level config, example wiki (P2, small each).

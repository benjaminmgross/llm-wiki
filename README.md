# llm-wiki

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**A folder-local CLI that turns any directory of mixed-type sources into an LLM-maintained wiki.**

Built on [Karpathy's llm-wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f). The wiki is a *persistent, compounding artifact* — each ingest doesn't just file the source, it weaves into existing pages, adds cross-references, and may produce synthesis writeups across what you already have. Sources are immutable. Pages are LLM-owned. You ask questions and curate; the LLM does the bookkeeping.

**v1.4.0 highlights (new):**

- **Packaged `mdwiki` agent skill** — `src/mdwiki/skill/` (symlinked at `skill/`) is an intent router plus one reference doc per operation; `mdwiki skill` prints the wiki's schema followed by the same files, and `mdwiki skill --workflow lint` prints one. Installable with `npx skills add benjaminmgross/llm-wiki`. The consolidator-era skill, references, and eight dead modules are gone.
- **Runtime pointer files at init** — `mdwiki init` writes thin `CLAUDE.md` and `AGENTS.md` (`--pointers=claude,codex,copilot`, or `none`) that tell an agent to run `mdwiki skill` and read `.mdwiki/schema.md`. Existing files are never overwritten; written pointers are never registered as sources.
- **`mdwiki search`** — FTS5 lexical search over page sections inside `state.db`: exact names, dates, identifiers, no model, no embedder, no new dependency. Maintained inside every transaction; session-ingest envelopes now carry `lexical_candidates`.
- **Contradictions end to end** — plans may record `contradictions[]` (quote-anchored like claims); mdwiki writes a managed `<!-- mdwiki:contradictions -->` block into the page, stores rows in `state.db`, counts them in `status`, and lint reports `unresolved-contradiction` once an entry stays pending across later ingests.
- **Navigation pages** — `wiki/concept-table.md` (one row per page: kind, sources, related, status, updated) regenerates with `index.md` on every transaction; `mdwiki overview` writes a model-authored `wiki/overview.md`, also via `ingest --pending --overview` and `refresh --bootstrap --overview`.
- **Source pages and frontmatter** — every ingest writes `wiki/sources/<id>-<slug>.md` (verdict, rationale, pages fed, anchored quotes, contradictions), and every page carries `title`, `type`, `created`, `updated`, `sources`, `tags` frontmatter for Obsidian and Dataview. `mdwiki rebuild --pages` now restores `backrefs` and `contradictions` from those pages.
- **Lint** — new `duplicate-candidate` (same-kind embedding cosine) and `isolated-cluster` rules; report grouped by severity with numbered findings; `--fix=1,3,7` applies a selection.
- **P2 polish** — `research` profile (`paper`/`claim`/`method`/`dataset`), a language-preservation rule in every schema and the ingest prompt, `Confidence:` lines and a file-worthiness hint on `query`, `query --stub` for recorded gaps, `query-filed`/`query-stub`/`overview` event kinds, a user-level `~/.config/mdwiki/config.toml` layer, and a committed example wiki at `examples/llm-wiki-pattern/`.

**v1.3.0 highlights:**

- **Native-session multi-agent corpus ingest** — an active Codex or Claude Code session can assign unique sources to read-only sub-agents for parallel plan generation, then validate and apply those plans serially with optimistic page hashes. This path uses the current session entitlement rather than separate API or local-inference charges.
- **`mdwiki refresh`** — re-scan an initialized wiki for newly-added files and register them as pending. Pair with `--bootstrap` for one-shot refreshes (`mdwiki refresh --bootstrap`); ideal for daily cron / scheduled-agent workflows. Content-hash dedup means previously-registered files are skipped automatically.
- **Provider-aware bootstrap ingest** — `init/refresh --bootstrap-batch` honors `.mdwiki/config.toml`. Providers with native batch support use it; other providers explicitly fall back to isolated synchronous ingest with the same backend. There is no silent Anthropic switch.
- **Durable partial-failure reporting** — failed sources retain their reason in `state.db` and `raw/.sources.json`; `mdwiki status` enumerates them and the next `--pending`/bootstrap run retries only pending or failed sources.
- **Sidecar merge fix** — `_register_sources` now merges with the existing `raw/.sources.json` instead of overwriting, so `mdwiki rebuild` continues to work correctly after a refresh.

**v1.2.0 highlights:**

- **Corpus-aware profiles** — `mdwiki init --profile=initiative|transcripts|framework|working-dir`. Each profile pre-bakes a tuned `schema.md` + `config.toml` overlay tailored to its corpus shape. `working-dir` is the legacy default.
- **`initiative` profile** — workhorse for project / strategy / cross-functional folders. Page kinds `decision` / `status` / `workstream` / `owner`.
- **`transcripts` profile** — meeting / call / interview corpora. Speaker-turn-aware chunker, `[HH:MM:SS]` timestamp-stripping quote-anchor mode, mandatory `person` entity per speaker, `meeting` / `decision` / `commitment` / `blocker` page kinds. New `TranscriptLoader` for `.vtt` / `.srt` / Fathom-style markdown.
- **`framework` profile** — procedure / template / assessment / learning four-kind taxonomy for "how-we-do-X" folders.
- **Page version chain** — every wiki/ page write embeds `previous_hash:` (SHA-256 of the prior body) in YAML frontmatter, building a tamper-evident chain. Inspired by [demarkus](https://github.com/latebit-io/demarkus).
- **Fallback chunker tiers** — `MarkdownChunker` now falls back from H2 → H1 → paragraph → sliding-window when no H2 is present. Transcripts and unstructured md no longer return zero sections.
- **`mdwiki skill`** — runtime command that prints the wiki's `.mdwiki/schema.md` plus an embedded agent how-to guide to stdout, so a Claude Code instance opening a wiki folder can `mdwiki skill` for inline docs.
- **Quality compounding primitives** (state.db tables + modules ready; full integration into ingest hot path is staged for v1.2.1):
  - `state.db.rejections` + `mdwiki.rejection_memory` — per-source rejection logging for prompt re-injection
  - `state.db.cost_ledger` + `mdwiki.cost_guard` — daily-USD spend tracking + budget enforcement
  - `[unverified-quote]` lint check flags any wiki page containing the marker
- **`canary` runner** — `scripts/run_canary.py` exercises mdwiki against a real folder and emits a Karpathy-style scorecard (page counts by kind, cross-ref density, coverage, backref count). Free structural mode + opt-in `--live` LLM mode.

**v1.1.0 (still shipped):**

- **Multi-filetype ingest** — markdown, plain text, code (35+ languages), CSV/TSV, PDF, DOCX, HTML; plus opt-in image OCR via Claude vision
- **Anthropic Batch API** — when `provider = "anthropic"`, `init --bootstrap-batch` submits every outstanding source as one batch (~50% cheaper, ~1h ETA)
- **OpenAI-compatible provider** — point at vLLM, llama.cpp, OpenRouter, Together, etc. for local/cheaper inference
- **Interactive `lint --fix`** — remediate broken refs deterministically; `--fix=full` re-ingests stale and coverage-gap sources
- **`rebuild-log`** — regenerate `wiki/log.md` from the events table (recovery utility)

> Replaces `markdown-consolidator`. v1.0.0 was a clean rewrite under the `mdwiki` package name; v1.4.0 removed the last consolidator-era modules and skill.

## Install

```bash
git clone <this repo>
cd llm-wiki
uv sync
# Only for provider = "anthropic":
export ANTHROPIC_API_KEY=sk-ant-...
```

The CLI binary lands at `.venv/bin/mdwiki`. Either `source .venv/bin/activate` or alias it:

```bash
alias mdwiki=~/path/to/llm-wiki/.venv/bin/mdwiki
```

## Quickstart

Point it at any folder of mixed sources:

```bash
cd ~/notes/research                  # any folder with .md / .pdf / .docx / .csv / .txt / code
mdwiki init --profile=working-dir    # scaffold .mdwiki/, register sources, write CLAUDE.md + AGENTS.md pointers
                                     # profiles: working-dir | initiative | transcripts | framework | research
mdwiki skill                         # print this wiki's schema + the agent skill (intent router + workflows)
mdwiki status                        # see "412 pending sources", failed reasons, pending contradictions
mdwiki doctor                        # verify provider + API ping
mdwiki ingest one-file.md            # interactive single-source ingest
mdwiki ingest --pending --overview   # bulk ingest everything pending or failed, then refresh wiki/overview.md
mdwiki session-ingest pending        # JSON manifest for native session sub-agent assignment
mdwiki refresh                       # later: pick up files added since init
mdwiki refresh --bootstrap           # ...and immediately ingest them (good for cron)
mdwiki search "attention sinks"      # lexical FTS5 search: exact names, dates, identifiers; no model
mdwiki query "what did I conclude about transformer attention sinks?"
mdwiki synthesize "fine-tuning vs RAG decision framework"
mdwiki overview                      # model-written top-level wiki/overview.md
mdwiki lint                          # severity-grouped health report; --fix, --fix=full, --fix=1,3,7
mdwiki undo                          # roll back the last transaction
```

Or skip the steps and bootstrap in one shot:

```bash
mdwiki init --bootstrap                          # init + ingest every pending source with --yes
mdwiki init --profile=initiative --bootstrap     # same, with a corpus-aware profile
```

## What gets created

After `mdwiki init` in your folder:

```
your-folder/
  CLAUDE.md              # runtime pointer: "run `mdwiki skill`, read .mdwiki/schema.md" (--pointers=claude)
  AGENTS.md              # same, for Codex-style agents (--pointers=codex); copilot adds .github/copilot-instructions.md
  .mdwiki/
    config.toml          # provider, model, embedder, [lint] thresholds, [pointers] files
    schema.md            # the LLM's playbook (page kinds, naming, cite-or-refuse rules, language rule)
    state.db             # local sqlite cache incl. the FTS5 search index (gitignored)
    write.lock           # sqlite-backed cross-process wiki write lock (gitignored)
    .gitignore           # ignores state.db + undo dirs
  raw/                   # immutable, content-addressed copies of your sources
    .sources.json        # mapping <hash> → original_path metadata
    a3f1b2c4d5e6-foo.md
    ...
  wiki/                  # LLM-maintained
    index.md             # generated catalog by page kind
    concept-table.md     # generated: one row per page — kind, sources, related, status, updated
    overview.md          # model-written top-level synthesis (`mdwiki overview`)
    log.md               # append-only event log
    entities/            # one page per entity (people, papers, projects, systems)
    concepts/            # one page per concept (ideas, techniques, patterns)
    syntheses/           # cross-cutting writeups
    sources/             # generated: one provenance page per ingested source
```

The folder *is* the wiki. Move it, copy it, share it — `mdwiki` re-discovers itself by walking up for `.mdwiki/` (like git).

## Profiles (corpus-aware seed schemas)

`mdwiki init --profile=<name>` picks a tuned `schema.md` + `config.toml` overlay tailored to your folder's shape. The profile is baked at init time; once initialized, the schema is yours to edit and the profile name is no longer authoritative.

| Profile | Best for | Page kinds added |
|---|---|---|
| `working-dir` (default) | Generic notes, mixed-topic working folders | `entity` / `concept` / `synthesis` only |
| `initiative` | Project / strategy / cross-functional folders (capital-raise, market-expansion, vc-scheduling-style) | `decision` / `status` / `workstream` / `owner` |
| `transcripts` | Meeting / call / interview corpora (`.vtt`, `.srt`, Fathom-style markdown). Speaker-turn-aware chunker; mandatory `person` entity per speaker | `meeting` / `decision` / `commitment` / `blocker` |
| `framework` | "How-we-do-X" folders — procedure / template / assessment / learning taxonomy | `procedure` / `template` / `assessment` / `learning` |
| `research` | Papers, preprints, reading notes, experiment logs, literature reviews | `paper` / `claim` / `method` / `dataset` |

```bash
mdwiki init --profile=initiative ~/dev/projects/capital-raise
mdwiki init --profile=transcripts ~/recordings/team-meetings
```

Profile schema files live at `src/mdwiki/profiles/<name>/schema.md` if you want to read or fork one before init.

## Keeping a wiki current — `mdwiki refresh`

`mdwiki init` registers every loadable file *that exists at init time*. New files dropped into the folder afterwards are invisible to the wiki until you re-run discovery. That's what `refresh` does:

```bash
cd ~/notes/research
# ... drop new files into the folder ...
mdwiki refresh                       # registers anything new as pending
mdwiki refresh --bootstrap           # ...and immediately ingests them (no prompts)
```

Refresh is content-hash dedup'd against `state.db`, so re-running it is safe and idempotent. Unchanged files are skipped; only genuinely new files become pending. Refresh adds; it never removes. If you delete a source file, the `raw/` copy and the wiki pages it produced stay; run `mdwiki lint` to flag the resulting orphans / coverage gaps. This makes `mdwiki refresh --bootstrap` the right one-liner for a daily cron or a scheduled-agent loop:

```bash
# crontab — every morning at 7am, sweep the folder for new files and ingest them
0 7 * * * cd ~/notes/research && /path/to/mdwiki refresh --bootstrap
# Use native batch when the configured provider supports it; otherwise this
# explicitly falls back to synchronous ingest with that same provider:
0 7 * * * cd ~/notes/research && /path/to/mdwiki refresh --bootstrap-batch -y
```

## Command reference

| Command | Purpose |
|---|---|
| `mdwiki init [path] [--profile=<name>] [--pointers=claude,codex,copilot\|none] [--bootstrap \| --bootstrap-batch]` | Scaffold `.mdwiki/`, register every loadable file as pending, write runtime pointer files (default `claude,codex`; existing files are never overwritten and written pointers are never sources). `--profile` selects a corpus-aware seed schema (default: `working-dir`). `--bootstrap` chains sync `ingest --pending --yes`; `--bootstrap-batch` uses configured-provider native batch when supported and an explicit same-provider sync fallback otherwise |
| `mdwiki refresh [path] [--bootstrap \| --bootstrap-batch] [--overview]` | Re-scan an initialized wiki for newly-added files, then optionally ingest outstanding pending/failed sources and refresh `wiki/overview.md` once at the end. `path` defaults to the current directory and may point anywhere inside the wiki tree. `--bootstrap-batch` never changes the configured provider. Ideal for daily cron / scheduled-agent workflows |
| `mdwiki skill [--workflow <name>]` | Print this wiki's `.mdwiki/schema.md` followed by the packaged agent skill (intent router + every reference workflow); `--workflow` prints one reference (works outside a wiki) |
| `mdwiki search "<terms>" [--limit N] [--json]` | FTS5 lexical search over page sections: terms are ANDed, the last term prefix-expanded, a `"quoted phrase"` kept adjacent. Ranked by bm25 with heading path and snippet. No provider, no embedder |
| `mdwiki status` | Pending/failed/ingested counts, enumerated failed-source reasons, page counts by kind, pending contradictions, recent events, last lint |
| `mdwiki source <hash-prefix>` | Inspect one registered source — original path, raw path, dependent pages |
| `mdwiki rebuild [--pages]` | Restore the `sources` table from `raw/.sources.json` after `state.db` deletion; `--pages` re-derives `pages`, embeddings, the search index, and `backrefs`/`contradictions` from `wiki/` (including the generated `wiki/sources/` pages) |
| `mdwiki rebuild-log` | Regenerate `wiki/log.md` from the events table (recovery utility) |
| `mdwiki doctor` | Pre-flight: provider config + 1-token API ping + embedder model |
| `mdwiki ingest <source>` | Interactive single-source ingest (prompt → JSON plan → quote-verify → confirm → apply) |
| `mdwiki ingest --pending [--overview]` | Bulk-ingest every source in pending or failed status. Resumes cleanly after interruption or partial failure. `--overview` refreshes `wiki/overview.md` once at the end |
| `mdwiki ingest --all` | Re-ingest every source, including already-ingested ones |
| `mdwiki session-ingest pending` | Emit a stable JSON manifest of pending/failed sources, once each, for parent-session assignment |
| `mdwiki session-ingest prepare <source> [-o envelope.json]` | Capture a read-only source/schema/page-hash envelope and plan contract without calling a provider or embedder |
| `mdwiki session-ingest apply <envelope.json>` | Revalidate a session-produced plan against latest source/schema/target pages and apply one serialized per-source transaction; exit 3 means re-prepare and retry |
| `mdwiki query "<q>" [--file] [--stub]` | Cited Q&A from existing wiki pages ending in a `Confidence: high\|medium\|low` line; the CLI suggests `--file` when the answer cites 3+ pages; `--file` files the answer as a synthesis (`query-filed` event); `--stub` records an unanswerable question as a `stub, needs-sources` concept page |
| `mdwiki synthesize "<topic>" \| --auto` | Explicit synthesis from a topic, or walk the cross-ref graph and propose syntheses |
| `mdwiki overview` | Write or refresh `wiki/overview.md` from `index.md`, `concept-table.md`, and pending contradictions (one provider call) |
| `mdwiki lint [--fix \| --fix=full \| --fix=1,3,7] [--yes]` | Health check grouped by severity with numbered findings: broken refs, unverified quotes, unresolved contradictions, orphans, stale pages, coverage gaps, duplicate candidates, isolated clusters. `--fix` strips broken refs deterministically; `--fix=full` re-ingests stale/coverage-gap sources; `--fix=<n>[,<n>...]` applies only those numbers from the last report |
| `mdwiki undo [N]` | Roll back the last N applied transactions (file writes + DB rows) |

## Supported filetypes (v1.1.0)

| Extension | Loader | Notes |
|---|---|---|
| `.md`, `.markdown` | `MarkdownLoader` | Passthrough |
| `.txt`, `.log`, `.rst` | `TextLoader` | Wrapped in a fenced code block |
| `.py`/`.js`/`.ts`/`.go`/`.rs`/`.java`/`.rb`/`.sh`/`.sql`/... (35+ extensions) | `CodeLoader` | Wrapped in a language-tagged fence |
| `.csv`, `.tsv` | `CsvLoader` | Converted to a markdown table; truncates to 100 rows |
| `.pdf` | `PdfLoader` | Text via pypdfium2; opt-in vision fallback for scanned PDFs |
| `.docx` | `DocxLoader` | Headings / paragraphs / tables preserved |
| `.html`, `.htm` | `HtmlLoader` | markdownify with `<script>`/`<style>`/`<noscript>` stripped |
| `.png`/`.jpg`/`.jpeg`/`.webp`/`.gif` | `ImageLoader` | Opt-in only; calls Claude vision (~$0.10–0.50/image) |

## How ingest works

1. Read source from `raw/`; chunk into H2 sections.
2. Embed each section locally (sentence-transformers, free).
3. ANN-search `pages.embedding` for the top candidates the LLM should know about.
4. Single LLM call: schema + `index.md` + candidate pages + sections → JSON plan of `{verdict, updates, new_pages, cross_refs, contradictions}`.
5. **Verify every quote** in the plan against the source — pure Python, no extra LLM call. Hallucinations get rejected. Contradiction entries are quote-anchored exactly like page claims.
6. Show the plan; you approve, edit, or reject (`--yes` to skip).
7. Apply inside a sqlite transaction with per-tx undo snapshot. Planned `cross_refs` become relative Markdown links in their source pages, `contradictions` become a managed `<!-- mdwiki:contradictions -->` block plus `state.db` rows, every written page gets `title/type/created/updated/sources/tags` frontmatter, a `wiki/sources/<id>-<slug>.md` provenance page is written, `index.md` and `concept-table.md` are regenerated, and the FTS5 search rows for touched pages are refreshed. `mdwiki undo` reverses all of it.

mdwiki delegates CommonMark interpretation—including inline and reference-style
links, titles, escapes, and code fences—to
[`markdown-it-py`](https://markdown-it-py.readthedocs.io/). Its own
cross-reference code is limited to wiki path policy and byte-preserving edits
inside `<!-- mdwiki:cross-refs -->` blocks. This boundary is intentional:
standardized syntax belongs to a maintained parser library, while mdwiki owns
only its domain-specific semantics. The same parser supplies exact source forms
for deterministic broken-link fixes. A fix is applied only when that source
form occurs uniquely, so identical examples in code spans or fences fail closed
instead of being rewritten. Transactional writes preserve explicit LF/CRLF
content and existing frontmatter newline style.

The LLM has explicit license to refuse: a source can verdict `low-quality`, `out-of-scope`, or `duplicate-of:<page>` instead of being force-fit into the wiki. Because an intentional refusal correctly produces no page backrefs, `mdwiki lint` does not report it as a coverage gap when the source's latest durable rejection or ingest event records one of those bounded verdicts. Matching is case-sensitive, `duplicate-of:` requires a non-whitespace page, and ingest events must match the complete production summary envelope (`<verdict>: <original_path> — <rationale>`). An ingest event wins when cross-store timestamps tie. An arbitrary verdict, a malformed summary, an older superseded refusal, or an ingested source with zero backrefs and no bounded refusal remains a coverage-gap finding.

All ingest transactions reserve a separate sqlite-backed wiki write lock before any file snapshot or replacement and hold it through database commit, sidecar mirroring, and log append. This protects page files, `index.md`, `state.db`, `raw/.sources.json`, and `log.md` across concurrent processes.

## Navigation pages, source pages, and frontmatter

mdwiki regenerates three surfaces inside every transaction that writes a page, so they can never drift from the pages themselves:

- `wiki/index.md` — the catalog by kind with one-line summaries (the model's entry point for query and synthesize).
- `wiki/concept-table.md` — one row per page from `state.db`, no model call: kind, distinct source count, related pages (outgoing links), status (`unsourced` / `single-source` / `multi-source` / `contradicted`), and last update.
- `wiki/sources/<id>-<slug>.md` — one page per ingested source: verdict, rationale, the pages it fed, every anchored quote with its section id, and contradictions recorded. Lint never reports these as orphans, they carry no embedding, and `mdwiki rebuild --pages` reads them to restore `backrefs` and `contradictions` after `state.db` is lost.

`wiki/overview.md` is the fourth surface and the only model-written one: `mdwiki overview` (or `--overview` on a batch) asks the provider for a 300–700 word orientation built from the index, the concept table, and the pending contradictions. All four are infrastructure pages: plans cannot target them and lint skips them.

Every page mdwiki writes carries frontmatter, so a wiki works from Obsidian (relative links, graph view) and Dataview (`type`, `sources`, `tags`) without post-processing:

```yaml
---
title: Attention Sinks
type: concept
created: 2026-09-02
updated: 2026-09-02
sources: [a3f1b2c4d5e6]
tags: [concept]
previous_hash: <sha256 of the prior body>
---
```

`sources` is the union of every source that has fed the page, `created` never changes, `updated` changes on every write, `tags` starts as the kind and keeps whatever you add, and `previous_hash` is computed on the body only so frontmatter edits never break the version chain.

## Contradictions

When a source disagrees with a page, the plan records `contradictions: [{page, existing_claim, source_claim, resolution, claims}]` instead of overwriting or dropping the claim. `resolution` is `pending`, `source-wins`, `existing-wins`, or `both-hold`. mdwiki appends the entry to the page's managed block, stores a row in `state.db`, shows the pending count in `mdwiki status`, marks the page `contradicted` in the concept table, and `mdwiki lint` raises `unresolved-contradiction` (warn) once an entry has stayed pending across `[lint].unresolved_contradiction_after` later ingests (default 3). A later plan that names the same pair with a resolution replaces it.

## Lexical search — `mdwiki search`

Embeddings find paraphrase; they miss exact names, dates, and identifiers, and native-session sub-agents run without an embedder at all. `mdwiki search "<terms>"` queries an FTS5 table of heading-bounded page sections kept inside `state.db`: no model, no embedder, no new dependency. Rows are refreshed inside every transaction and rebuilt by `undo` and `rebuild --pages`; `session-ingest prepare` puts the top lexical matches for the source in the envelope as `wiki.lexical_candidates`. Agents are expected to search before creating a page.

## Agent skill and pointer files

`src/mdwiki/skill/SKILL.md` is an intent router (bootstrap, ingest, session-ingest, search, query, synthesize, overview, lint, contradictions, navigation, undo) backed by one reference file each under `references/`. The repository-root `skill/` is a symlink to it so `npx skills add benjaminmgross/llm-wiki` installs the same bytes `mdwiki skill` prints at runtime. `mdwiki init` writes thin `CLAUDE.md` and `AGENTS.md` pointers (`--pointers=claude,codex,copilot`, or `none`) that send an agent to `mdwiki skill` and `.mdwiki/schema.md`; they hold no operating rules, are never overwritten, and are recorded under `[pointers].files` so neither `init` nor `refresh` registers them as sources.

## Native-session multi-agent corpus ingest

`session-ingest` is the deterministic handoff for an active Codex or Claude Code session. mdwiki does not spawn agents itself, and this path never calls `mdwiki.llm`, a model SDK/HTTP endpoint, or the local embedder.

1. The parent runs `mdwiki session-ingest pending` and assigns every listed source to exactly one native session sub-agent. Planning concurrency is bounded by the active session's actual agent-slot limit.
2. Each worker stays read-only and runs `mdwiki session-ingest prepare <source> -o .mdwiki/session-plans/<source-id>.json`. Keeping envelopes under `.mdwiki/` prevents a later refresh from registering them as corpus sources. The envelope contains source sections, schema, current page paths/hashes, agent instructions, and the exact plan JSON contract. The worker fills only `plan`.
3. The parent applies completed envelopes one at a time with `mdwiki session-ingest apply <envelope>.json`.
4. Apply acquires the wiki-wide write lock, rechecks source/configuration/schema hashes, and compares every write/read-modify-write page (including a cross-reference `from_page`) with its analysis-time hash. A target-only cross-reference endpoint needs only to continue existing under the lock. Unrelated and target-only content changes do not invalidate a plan; policy changes, overlapping writes, deleted endpoints, and new-page races do.
5. Exit code 3 means the plan was invalidated. The parent re-prepares and re-plans that source, capped at three retries, while continuing other sources. Successful sources remain independently committed and undoable; interrupted or exhausted work remains pending/failed for the next manifest.

Because session plans deliberately avoid local inference, their page rows carry no embedding until a later provider-backed ingest or `mdwiki rebuild --pages` refreshes the derived embedding cache. Page content, citations, relative cross-reference links, transactions, source state, index, log, and undo remain complete.

## Configuration

`.mdwiki/config.toml` after init:

```toml
[llm]
provider = "anthropic"
model = "claude-sonnet-4-6"

[embedder]
model = "sentence-transformers/all-MiniLM-L6-v2"

[ingest]
candidate_top_k = 8
max_undo_history = 50

# v1.1.0 — opt-in loaders (cost-sensitive)
[loaders.image]
enabled = false  # set true to ingest .png/.jpg/etc via Claude vision (~$0.10–0.50/image)

[loaders.pdf]
vision_fallback = false  # set true to OCR scanned PDFs via Claude vision when text extraction is empty
```

### Local providers (v1.1.0)

To run against a vLLM / llama.cpp / OpenRouter / Together endpoint:

```toml
[llm]
provider = "openai-compatible"
model = "qwen2.5-72b-instruct"   # whatever your endpoint serves

[llm.openai_compatible]
base_url = "http://localhost:8000/v1"
api_key = "not-needed-for-vllm"  # optional; required for OpenRouter / Together
timeout = 120.0
vision_capable = false           # set true only for vision-capable models (Qwen-VL, LLaVA)
```

`api_key` is read as a literal config value; mdwiki does not expand environment-variable placeholders in TOML. If a hosted endpoint requires a secret, keep the secret-bearing config out of version control or apply your repository's secret-management policy.

`--bootstrap-batch` works with this configuration. Because most OpenAI-compatible endpoints do not expose a native batch API, mdwiki prints an explicit notice and processes sources sequentially through the configured OpenAI-compatible endpoint. It never substitutes Anthropic. Each source commits independently; failures remain retryable and appear with reasons in `mdwiki status`.

After setting the OpenAI-compatible block above in the wiki's `.mdwiki/config.toml`, the terminal-driven capital-raise workflow is:

```bash
cd /path/to/key-initiatives/capital-raise
mdwiki doctor                         # verifies this OpenAI-compatible endpoint/model
mdwiki refresh --bootstrap-batch -y  # explicit same-provider sync fallback when native batch is unavailable
mdwiki status                         # 0 pending/failed, or exact failed paths + reasons
```

No `wiki/`, `raw/`, log, backref, or database edits are required. If one source fails, successful sources remain committed and the next `refresh --bootstrap-batch -y` or `ingest --pending` run selects only sources still marked pending or failed.

`describe_image` still requires `vision_capable = true` and a model/endpoint that actually accepts image inputs.

`.mdwiki/schema.md` is the LLM's rulebook — page kinds, naming conventions, citation format, when to update vs create, synthesis triggers, contradictions, and the language rule (page prose in the source's language; keys, kinds, filenames, and headings in English). It ships with sane defaults; edit to taste, the LLM honors it on every ingest/query/synthesize.

### User-level config

`${XDG_CONFIG_HOME:-~/.config}/mdwiki/config.toml` is deep-merged *under* every wiki's `.mdwiki/config.toml` when mdwiki builds a provider or runs `doctor`. Put the shared `[llm]` block and an OpenAI-compatible `api_key` there once instead of in every wiki (and out of version control); the wiki file wins on any key it sets. Per-wiki knobs (profile, exclusions, pointer files, `[lint]` thresholds) are read from the wiki file only.

### Lint thresholds

```toml
[lint]
unresolved_contradiction_after = 3   # later ingests a pending contradiction may survive before lint warns
duplicate_similarity = 0.92          # same-kind embedding cosine at or above which two pages are duplicate candidates
```

### Editor integration

Pages use relative Markdown links and standard YAML frontmatter, so an mdwiki folder opens as an Obsidian vault as-is (graph view follows the cross-references; Dataview can query `type`, `sources`, `tags`, `updated`). Keep `.mdwiki/` and `raw/` out of Obsidian's attachment folder and let `wiki/index.md`, `wiki/concept-table.md`, and `wiki/sources/` be read-only; mdwiki regenerates them.

### Example wiki

`examples/llm-wiki-pattern/` is a real mdwiki built deterministically by `scripts/build_example_wiki.py` from three notes about the pattern: it shows cross-references, a recorded contradiction, source pages, frontmatter, the concept table, and pointer files. `tests/test_example_wiki.py` rebuilds its `state.db` (`mdwiki rebuild` + `rebuild --pages`) and asserts it lints clean, so it doubles as a fixture.

## v1.1.0 non-goals

Designed for, deferred to v1.2+:

- **Streaming native-batch status** — when the configured provider uses native batch, `--bootstrap-batch` polls every 60s and reports progress, but doesn't expose the underlying batch object
- **Loader registry pluggable via entry_points** — third-party loaders are v1.2+
- **Audio / video / xlsx loaders** — punted
- **Multi-provider dispatch within one wiki** — config still names ONE provider
- **`--verbose` flag** — first-time `init --bootstrap` is too silent; planned for v1.1.1
- Web UI, multi-user collaboration, real-time preview, cross-machine `state.db` sync, scheduled background ingest

## Architecture

See [`docs/mdwiki-design.md`](docs/mdwiki-design.md) for the locked v1.0.0 spec, including the three-layer architecture, ingest algorithm, hallucination guards (cite-or-refuse + quote-anchor + anti-sycophancy), and the cost model calibrated against real corpora.

## License

MIT

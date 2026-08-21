# llm-wiki

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**A folder-local CLI that turns any directory of mixed-type sources into an LLM-maintained wiki.**

Built on [Karpathy's llm-wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f). The wiki is a *persistent, compounding artifact* — each ingest doesn't just file the source, it weaves into existing pages, adds cross-references, and may produce synthesis writeups across what you already have. Sources are immutable. Pages are LLM-owned. You ask questions and curate; the LLM does the bookkeeping.

**v1.3.0 highlights (new):**

- **`mdwiki refresh`** — re-scan an initialized wiki for newly-added files and register them as pending. Pair with `--bootstrap` for one-shot refreshes (`mdwiki refresh --bootstrap`); ideal for daily cron / scheduled-agent workflows. Content-hash dedup means previously-registered files are skipped automatically.
- **Provider-aware bootstrap ingest** — `init/refresh --bootstrap-batch` honors `.mdwiki/config.toml`. Providers with native batch support use it; other providers explicitly fall back to isolated synchronous ingest with the same backend. There is no silent Anthropic switch.
- **Durable partial-failure reporting** — failed sources retain their reason in `state.db` and `raw/.sources.json`; `mdwiki status` enumerates them and the next `--pending`/bootstrap run retries only pending or failed sources.
- **Sidecar merge fix** — `_register_sources` now merges with the existing `raw/.sources.json` instead of overwriting, so `mdwiki rebuild` continues to work correctly after a refresh.
- **Pre-registration source scoping** — repeatable `mdwiki init --exclude <gitwildmatch>` patterns are persisted to `[exclude].globs` and applied before files enter `raw/`; refresh reuses the same rules.
- **Native-batch cost guard and receipts** — `--daily-budget-usd` persists a conservative cap, bootstrap rejects a projected over-budget batch before submission, and successful native batches write an estimated-cost ledger row and print a receipt. Use `--no-cost-guard` only for an explicitly approved bootstrap exemption.

**v1.2.0 highlights:**

- **Corpus-aware profiles** — `mdwiki init --profile=initiative|transcripts|framework|working-dir`. Each profile pre-bakes a tuned `schema.md` + `config.toml` overlay tailored to its corpus shape. `working-dir` is the legacy default.
- **`initiative` profile** — workhorse for project / strategy / cross-functional folders. Page kinds `decision` / `status` / `workstream` / `owner`.
- **`transcripts` profile** — meeting / call / interview corpora. Speaker-turn-aware chunker, `[HH:MM:SS]` timestamp-stripping quote-anchor mode, mandatory `person` entity per speaker, `meeting` / `decision` / `commitment` / `blocker` page kinds. New `TranscriptLoader` for `.vtt` / `.srt` / Fathom-style markdown.
- **`framework` profile** — procedure / template / assessment / learning four-kind taxonomy for "how-we-do-X" folders.
- **Page version chain** — every wiki/ page write embeds `previous_hash:` (SHA-256 of the prior body) in YAML frontmatter, building a tamper-evident chain. Inspired by [demarkus](https://github.com/latebit-io/demarkus).
- **Fallback chunker tiers** — `MarkdownChunker` now falls back from H2 → H1 → paragraph → sliding-window when no H2 is present. Transcripts and unstructured md no longer return zero sections.
- **`mdwiki skill`** — runtime command that prints the wiki's `.mdwiki/schema.md` plus an embedded agent how-to guide to stdout, so a Claude Code instance opening a wiki folder can `mdwiki skill` for inline docs.
- **Quality compounding primitives:**
  - `state.db.rejections` + `mdwiki.rejection_memory` — per-source rejection logging for prompt re-injection
  - `state.db.cost_ledger` + `mdwiki.cost_guard` — native-batch projected-spend enforcement and estimated-cost receipts; other LLM paths remain follow-up wiring
  - `[unverified-quote]` lint check flags any wiki page containing the marker
- **`canary` runner** — `scripts/run_canary.py` exercises mdwiki against a real folder and emits a Karpathy-style scorecard (page counts by kind, cross-ref density, coverage, backref count). Free structural mode + opt-in `--live` LLM mode.

**v1.1.0 (still shipped):**

- **Multi-filetype ingest** — markdown, plain text, code (35+ languages), CSV/TSV, PDF, DOCX, HTML; plus opt-in image OCR via Claude vision
- **Anthropic Batch API** — when `provider = "anthropic"`, `init --bootstrap-batch` submits every outstanding source as one batch (~50% cheaper, ~1h ETA)
- **OpenAI-compatible provider** — point at vLLM, llama.cpp, OpenRouter, Together, etc. for local/cheaper inference
- **Interactive `lint --fix`** — remediate broken refs deterministically; `--fix=full` re-ingests stale and coverage-gap sources
- **`rebuild-log`** — regenerate `wiki/log.md` from the events table (recovery utility)

> Replaces `markdown-consolidator`. v1.0.0 was a clean rewrite under the `mdwiki` package name.

## Install

```bash
git clone <this repo>
cd markdown-consolidator
uv sync
# Only for provider = "anthropic":
export ANTHROPIC_API_KEY=sk-ant-...
```

The CLI binary lands at `.venv/bin/mdwiki`. Either `source .venv/bin/activate` or alias it:

```bash
alias mdwiki=~/path/to/markdown-consolidator/.venv/bin/mdwiki
```

## Quickstart

Point it at any folder of mixed sources:

```bash
cd ~/notes/research                  # any folder with .md / .pdf / .docx / .csv / .txt / code
mdwiki init --profile=working-dir    # scaffold .mdwiki/, register sources, ingest nothing yet
                                     # profiles: working-dir | initiative | transcripts | framework
                                     # use repeatable --exclude globs to scope before registration
                                     # add --daily-budget-usd 2 for a conservative native-batch cap
mdwiki status                        # see "412 pending sources"
mdwiki doctor                        # verify provider + API ping
mdwiki ingest one-file.md            # interactive single-source ingest
mdwiki ingest --pending              # bulk ingest everything pending or previously failed
mdwiki refresh                       # later: pick up files added since init
mdwiki refresh --bootstrap           # ...and immediately ingest them (good for cron)
mdwiki query "what did I conclude about transformer attention sinks?"
mdwiki synthesize "fine-tuning vs RAG decision framework"
mdwiki lint                          # broken refs, orphans, stale pages, coverage gaps
mdwiki undo                          # roll back the last transaction
```

Or skip the steps and bootstrap in one shot:

```bash
mdwiki init --bootstrap                          # init + ingest every pending source with --yes
mdwiki init --profile=initiative --bootstrap     # same, with a corpus-aware profile
mdwiki init --profile=framework --exclude 'generated/**' --daily-budget-usd 2
mdwiki refresh --bootstrap-batch --yes --no-cost-guard  # explicit bootstrap-only budget exemption
```

## What gets created

After `mdwiki init` in your folder:

```
your-folder/
  .mdwiki/
    config.toml          # provider, model, embedder
    schema.md            # the LLM's playbook (page kinds, naming, cite-or-refuse rules)
    state.db             # local sqlite cache (gitignored)
    .gitignore           # ignores state.db + undo dirs
  raw/                   # immutable, content-addressed copies of your sources
    .sources.json        # mapping <hash> → original_path metadata
    a3f1b2c4d5e6-foo.md
    ...
  wiki/                  # LLM-maintained
    index.md             # auto-generated catalog by page kind
    log.md               # append-only event log
    entities/            # one page per entity (people, papers, projects, systems)
    concepts/            # one page per concept (ideas, techniques, patterns)
    syntheses/           # cross-cutting writeups
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
| `mdwiki init [path] [--profile=<name>] [--exclude <glob>] [--daily-budget-usd <usd>] [--bootstrap \| --bootstrap-batch]` | Scaffold `.mdwiki/`, persist source exclusions and the optional native-batch budget, then register only allowed files as pending. `--exclude` is repeatable. `--bootstrap` chains sync `ingest --pending --yes`; `--bootstrap-batch` uses configured-provider native batch when supported and an explicit same-provider sync fallback otherwise |
| `mdwiki refresh [path] [--bootstrap \| --bootstrap-batch] [--no-cost-guard]` | Re-scan an initialized wiki using the persisted exclusions, then optionally ingest outstanding pending/failed sources. Native batch enforces the configured projected-spend cap and prints a receipt; `--no-cost-guard` is an explicit one-run exemption. `--bootstrap-batch` never changes the configured provider |
| `mdwiki status` | Pending/failed/ingested counts, enumerated failed-source reasons, page counts by kind, recent events, last lint |
| `mdwiki source <hash-prefix>` | Inspect one registered source — original path, raw path, dependent pages |
| `mdwiki rebuild` | Restore the `sources` table from `raw/.sources.json` after `state.db` deletion |
| `mdwiki rebuild-log` | Regenerate `wiki/log.md` from the events table (recovery utility) |
| `mdwiki doctor` | Pre-flight: provider config + 1-token API ping + embedder model |
| `mdwiki ingest <source>` | Interactive single-source ingest (prompt → JSON plan → quote-verify → confirm → apply) |
| `mdwiki ingest --pending` | Bulk-ingest every source in pending or failed status. Resumes cleanly after interruption or partial failure |
| `mdwiki ingest --all` | Re-ingest every source, including already-ingested ones |
| `mdwiki query "<q>" [--file]` | Cited Q&A from existing wiki pages; `--file` files the answer as a synthesis |
| `mdwiki synthesize "<topic>" \| --auto` | Explicit synthesis from a topic, or walk the cross-ref graph and propose syntheses |
| `mdwiki lint [--fix [=full]]` | Health check (broken refs, orphans, stale, coverage gaps). `--fix` strips broken refs deterministically; `--fix=full` re-ingests stale/coverage-gap sources |
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
4. Single LLM call: schema + `index.md` + candidate pages + sections → JSON plan of `{verdict, updates, new_pages, cross_refs}`.
5. **Verify every quote** in the plan against the source — pure Python, no extra LLM call. Hallucinations get rejected.
6. Show the plan; you approve, edit, or reject (`--yes` to skip).
7. Apply inside a sqlite transaction with per-tx undo snapshot. `mdwiki undo` reverses it.

The LLM has explicit license to refuse: a source can verdict `low-quality`, `out-of-scope`, or `duplicate-of:<page>` instead of being force-fit into the wiki.

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

`.mdwiki/schema.md` is the LLM's rulebook — page kinds, naming conventions, citation format, when to update vs create, synthesis triggers. It ships with sane defaults; edit to taste, the LLM honors it on every ingest/query/synthesize.

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

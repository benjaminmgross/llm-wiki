# mdwiki

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**A folder-local CLI that turns any directory of mixed-type sources into an LLM-maintained wiki.**

Built on [Karpathy's llm-wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f). The wiki is a *persistent, compounding artifact* — each ingest doesn't just file the source, it weaves into existing pages, adds cross-references, and may produce synthesis writeups across what you already have. Sources are immutable. Pages are LLM-owned. You ask questions and curate; the LLM does the bookkeeping.

**v1.1.0 highlights:**

- **Multi-filetype ingest** — markdown, plain text, code (35+ languages), CSV/TSV, PDF, DOCX, HTML; plus opt-in image OCR via Claude vision
- **Anthropic Batch API** — `init --bootstrap-batch` submits every pending source as one batch (~50% cheaper, ~1h ETA)
- **OpenAI-compatible provider** — point at vLLM, llama.cpp, OpenRouter, Together, etc. for local/cheaper inference
- **Interactive `lint --fix`** — remediate broken refs deterministically; `--fix=full` re-ingests stale and coverage-gap sources
- **`rebuild-log`** — regenerate `wiki/log.md` from the events table (recovery utility)

> Replaces `markdown-consolidator`. v1.0.0 was a clean rewrite under the `mdwiki` package name.

## Install

```bash
git clone <this repo>
cd markdown-consolidator
uv sync
export ANTHROPIC_API_KEY=sk-ant-...
```

The CLI binary lands at `.venv/bin/mdwiki`. Either `source .venv/bin/activate` or alias it:

```bash
alias mdwiki=~/path/to/markdown-consolidator/.venv/bin/mdwiki
```

## Quickstart

Point it at any folder of markdown:

```bash
cd ~/notes/research          # any folder with .md files
mdwiki init                  # scaffold .mdwiki/, register sources, ingest nothing yet
mdwiki status                # see "412 pending sources"
mdwiki doctor                # verify provider + API ping
mdwiki ingest one-file.md    # interactive single-source ingest
mdwiki ingest --pending      # bulk ingest everything that's still pending
mdwiki query "what did I conclude about transformer attention sinks?"
mdwiki synthesize "fine-tuning vs RAG decision framework"
mdwiki lint                  # broken refs, orphans, stale pages, coverage gaps
mdwiki undo                  # roll back the last transaction
```

Or skip the steps and bootstrap in one shot:

```bash
mdwiki init --bootstrap      # init + ingest every pending source with --yes
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

## Command reference

| Command | Purpose |
|---|---|
| `mdwiki init [path] [--bootstrap \| --bootstrap-batch]` | Scaffold `.mdwiki/`, register every loadable file as pending. `--bootstrap` chains sync `ingest --pending --yes`; `--bootstrap-batch` submits every pending source via Anthropic's Batch API (~50% cheaper, ~1h ETA) |
| `mdwiki status` | Pending/ingested counts, page counts by kind, recent events, last lint |
| `mdwiki source <hash-prefix>` | Inspect one registered source — original path, raw path, dependent pages |
| `mdwiki rebuild` | Restore the `sources` table from `raw/.sources.json` after `state.db` deletion |
| `mdwiki rebuild-log` | Regenerate `wiki/log.md` from the events table (recovery utility) |
| `mdwiki doctor` | Pre-flight: provider config + 1-token API ping + embedder model |
| `mdwiki ingest <source>` | Interactive single-source ingest (prompt → JSON plan → quote-verify → confirm → apply) |
| `mdwiki ingest --pending` | Bulk-ingest every source still in pending status. Resumes cleanly if interrupted |
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

Caveats: `--bootstrap-batch` requires `provider = "anthropic"` (most local servers don't have a batch API); `describe_image` requires `vision_capable = true`.

`.mdwiki/schema.md` is the LLM's rulebook — page kinds, naming conventions, citation format, when to update vs create, synthesis triggers. It ships with sane defaults; edit to taste, the LLM honors it on every ingest/query/synthesize.

## v1.1.0 non-goals

Designed for, deferred to v1.2+:

- **Streaming Batch API status** — `--bootstrap-batch` polls every 60s and reports progress, but doesn't expose the underlying batch object
- **Loader registry pluggable via entry_points** — third-party loaders are v1.2+
- **Audio / video / xlsx loaders** — punted
- **Multi-provider dispatch within one wiki** — config still names ONE provider
- **`--verbose` flag** — first-time `init --bootstrap` is too silent; planned for v1.1.1
- Web UI, multi-user collaboration, real-time preview, cross-machine `state.db` sync, scheduled background ingest

## Architecture

See [`docs/mdwiki-design.md`](docs/mdwiki-design.md) for the locked v1.0.0 spec, including the three-layer architecture, ingest algorithm, hallucination guards (cite-or-refuse + quote-anchor + anti-sycophancy), and the cost model calibrated against real corpora.

## License

MIT

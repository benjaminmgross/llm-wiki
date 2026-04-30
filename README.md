# mdwiki

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**A folder-local CLI that turns any directory of markdown into an LLM-maintained wiki.**

Built on [Karpathy's llm-wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f). The wiki is a *persistent, compounding artifact* — each ingest doesn't just file the source, it weaves into existing pages, adds cross-references, and may produce synthesis writeups across what you already have. Sources are immutable. Pages are LLM-owned. You ask questions and curate; the LLM does the bookkeeping.

> **Replaces `markdown-consolidator`.** This is a clean v1.0.0 rewrite under a new package name. The old `mdconsolidate` CLI is removed; the legacy modules (chunker, embedder, clustering, synthesis) live on as internals.

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
| `mdwiki init [path] [--bootstrap]` | Scaffold `.mdwiki/`, register every `.md` as pending. `--bootstrap` chains `ingest --pending --yes` |
| `mdwiki status` | Pending/ingested counts, page counts by kind, recent events, last lint |
| `mdwiki source <hash-prefix>` | Inspect one registered source — original path, raw path, dependent pages |
| `mdwiki rebuild` | Restore the `sources` table from `raw/.sources.json` after `state.db` deletion. v1.0.0 does NOT replay `backrefs`/`pages`/`events` (the log format is too lossy); re-ingest sources to recover them |
| `mdwiki doctor` | Pre-flight: provider config + 1-token API ping + embedder model |
| `mdwiki ingest <source>` | Interactive single-source ingest (prompt → JSON plan → quote-verify → confirm → apply) |
| `mdwiki ingest --pending` | Bulk-ingest every source still in pending status (`--yes` implied). Each source is its own atomic transaction — if a bulk run is interrupted, re-running `--pending` resumes from where it stopped |
| `mdwiki ingest --all` | Re-ingest every source, including already-ingested ones |
| `mdwiki query "<q>"` | Answer a question with citations from existing wiki pages |
| `mdwiki query "<q>" --file` | …and file the answer as a synthesis page |
| `mdwiki synthesize "<topic>"` | Explicit synthesis page from a topic + the wiki's most-relevant pages |
| `mdwiki synthesize --auto` | Walk cross-ref graph; LLM proposes syntheses for each cluster |
| `mdwiki lint` | Broken refs, orphans, stale pages, coverage gaps |
| `mdwiki undo [N]` | Roll back the last N applied transactions (file writes + DB rows) |

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
```

`.mdwiki/schema.md` is the LLM's rulebook — page kinds, naming conventions, citation format, when to update vs create, synthesis triggers. It ships with sane defaults; edit to taste, the LLM honors it on every ingest/query/synthesize.

## v1.0.0 non-goals

Designed for, deferred to v1.1+:

- **Local model providers** (Qwen, Kimi) — `Provider` ABC seam exists in `src/mdwiki/llm/`
- **Multi-filetype ingest** — v1.0.0 reads `.md` only; PDF / DOCX / CSV / images via vision OCR planned for v1.1
- **Batch API for `init --bootstrap`** — v1.0.0 chains sync ingest. Batch (~50% cheaper, ~1h turnaround) is a v1.1 cost-optimization
- Web UI, multi-user collaboration, real-time preview, cross-machine `state.db` sync, scheduled background ingest

## Architecture

See [`docs/mdwiki-design.md`](docs/mdwiki-design.md) for the locked v1.0.0 spec, including the three-layer architecture, ingest algorithm, hallucination guards (cite-or-refuse + quote-anchor + anti-sycophancy), and the cost model calibrated against real corpora.

## License

MIT

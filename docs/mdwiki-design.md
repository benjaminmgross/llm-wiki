# mdwiki — Design Doc (v0)

**Vision.** A folder-local CLI that turns any directory of markdown into an LLM-maintained wiki: immutable raw sources, an LLM-owned wiki layer of interlinked pages, and a schema document the user controls. Built on Karpathy's *llm-wiki* pattern. Replaces `markdown-consolidator`; the existing modules become internals.

## UX (the happy path)

```bash
$ cd ~/notes/research        # a folder with hundreds of .md scattered in subdirs
$ mdwiki init                # scaffolds .mdwiki/, ingests nothing yet
$ mdwiki status              # 412 pending sources, 0 wiki pages
$ mdwiki ingest --all        # incremental loop; logs every page touched
$ mdwiki query "what did I conclude about transformer attention sinks?"
$ mdwiki lint                # contradictions, orphans, stale, header quality
```

Every command discovers the wiki by walking up for `.mdwiki/` (like `git`). The folder *is* the wiki — config, sqlite, schema, raw, and wiki/ all live inside it.

## Directory layout

```
my-wiki/
  .mdwiki/
    config.toml          # model, thresholds, exclude globs
    state.db             # sqlite: sources, pages, backrefs, events, embeddings
    schema.md            # symlinked to ../CLAUDE.md (the schema IS the Claude config)
  raw/                   # immutable, append-only, flat (see below)
  wiki/
    index.md             # LLM-maintained catalog by category
    log.md               # append-only event log (mirrors state.db events)
    entities/            # one page per entity
    concepts/            # one page per concept
    syntheses/           # cross-cutting writeups
  CLAUDE.md              # schema: page types, conventions, ingest/query/lint rules
```

### Answer to Q2 (raw is flat; folder structure becomes metadata)

`raw/` is **flat with content-addressed names** (`raw/<sha256[:12]>-<slug>.md`). Original folder paths are stored in `state.db` as `original_path` and surfaced to the LLM as a *hint* during ingest, not as a constraint. Rationale:

- The wiki layer is the only place "structure" should live, and the LLM owns it.
- Flattening avoids two competing taxonomies (filesystem vs wiki).
- Provenance is preserved without coupling.
- `mdwiki source <id>` resurfaces original path on demand.

No `--auto-sort` of raw — there is nothing to sort. Auto-organization happens in `wiki/` and is the LLM's job. (`--mirror-raw` stays as an escape hatch for users who insist.)

## Initialize behavior — answer to Q1

`mdwiki init` is **non-destructive and idempotent**:

1. Create `.mdwiki/` with default config, empty sqlite schema, and a starter `CLAUDE.md`.
2. Walk the folder (respecting `.gitignore` + config excludes) and **register** every `.md` as a `pending` source: hash, mtime, original_path, copied to `raw/`.
3. Print a summary; **ingest nothing**.

Then the user picks how to bootstrap:

| Mode | Command | When to use |
|---|---|---|
| Incremental | `mdwiki ingest <file>` or `--all` | Default; one source at a time, ~10–15 page touches each, fully attributable. |
| Bootstrap | `mdwiki init --bootstrap` | Cold start with hundreds of files; runs the legacy clustering pipeline once to seed `wiki/`, then switches to incremental. |
| Lazy | (do nothing) | Sources stay `pending`; only ingested when referenced by a query. |

Bootstrap is where today's `clustering` + `synthesis` modules earn their keep — they solve the cold-start problem the wiki pattern is weakest at.

## Schema layer (`CLAUDE.md`)

A user-editable markdown file defining: page kinds (`entity`, `concept`, `synthesis`), naming/linking conventions, when to create vs update a page, citation format, lint policies. Ships with a sane default. Because it lives at the repo root and is named `CLAUDE.md`, Claude Code picks it up automatically — the wiki *is* a Claude project.

## State schema (sqlite)

- `sources(id, original_path, raw_path, content_hash, mtime, ingested_at, status)`
- `pages(path, kind, embedding BLOB, last_touched_at)`
- `backrefs(page_path, source_id, section_anchor, confidence)`
- `events(ts, kind, source_id, page_paths_json, summary)` — mirrors `log.md`
- `embeddings(text_hash, vector BLOB)` — cache

## Ingest algorithm (one source)

1. Hash + register; copy to `raw/` if new.
2. Chunk (`chunker.py`); embed each section (`embedder.py`).
3. For each section: ANN search over `pages.embedding` → candidate pages.
4. Single LLM call with: section + candidates + `CLAUDE.md` schema → JSON plan of `{updates, new_pages, cross_refs}`.
5. Apply edits to `wiki/`; append to `log.md`; insert `events` + `backrefs` rows; mark source `ingested`.
6. Periodically run `mdwiki lint` (or `--lint-after`) to catch drift.

## Command surface

| Command | Purpose |
|---|---|
| `mdwiki init [--bootstrap] [--mirror-raw]` | Scaffold + register sources |
| `mdwiki ingest [<file>\|--all\|--pending]` | Incremental ingest |
| `mdwiki query "<q>" [--file]` | Search + synthesize, optionally file findings as a new page |
| `mdwiki lint [--fix]` | Contradictions, orphans, stale, focus, headers, gaps |
| `mdwiki status` | Pending/ingested counts, last lint, recent events |
| `mdwiki source <id>` | Reveal raw path / original path / dependent pages |

## Migration stance

Drop `mdconsolidate` as a public CLI. The existing modules (`inventory`, `chunker`, `embedder`, `clustering`, `synthesis`, `encapsulation`, `summarizer`, `tree_builder`, `keywords`, plus the in-flight `header_coherence` and `outline_consolidator`) become **internals of `mdwiki`**, called by `init --bootstrap`, `ingest`, and `lint`. No backwards-compat shims, no parallel front-end. Project rename: `markdown-consolidator` → `mdwiki`. Version: ship as **v1.0.0** under the new name; old package gets a final v0.3.0 release noting the rename.

## Open questions

- **Conflict UX during ingest** — auto-apply, dry-run + diff, or always interactive?
- **Where does the LLM run** — local Anthropic API call per ingest, or batchable? (Cost ceiling matters at 412-source bootstrap.)
- **Wiki-edit safety** — write directly, or stage to `.mdwiki/pending-edits/` for review?
- **Multi-wiki** — one folder = one wiki, or allow nested wikis? (Recommend: single, like git.)
- **Sync** — is `state.db` checked in, or `.gitignore`d and rebuildable from `raw/` + `wiki/`?

Pick answers to the open questions and the v1.0.0 milestone is well-defined.

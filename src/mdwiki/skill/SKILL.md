---
name: mdwiki
description: "Operate an mdwiki folder — an LLM-maintained wiki built from a directory of mixed sources (Karpathy llm-wiki pattern). Use when a folder contains .mdwiki/, when asked to bootstrap a wiki from notes, ingest new sources, search or query a wiki, synthesize across pages, lint or repair wiki health, or roll back a wiki change."
---

# mdwiki

`mdwiki` is a folder-local CLI. The folder *is* the wiki: `raw/` holds immutable content-addressed copies of sources, `wiki/` holds LLM-owned pages, `.mdwiki/` holds the schema, config, and a rebuildable sqlite cache. Every write goes through a transaction with an undo snapshot, every claim must quote its source verbatim, and the LLM may refuse a source instead of force-fitting it.

Before any operation, run `mdwiki skill` inside the wiki and read `.mdwiki/schema.md`; the schema decides page kinds, naming, citation format, and refusal policy for *this* wiki. Never write to `raw/`. Never hand-edit `wiki/index.md`, `wiki/concept-table.md`, `wiki/log.md`, or `wiki/sources/`; they are regenerated.

## Intent router

| You want to… | Run | Read |
|---|---|---|
| Turn a folder into a wiki, or ingest everything pending in one shot | `mdwiki init [--profile=<name>] [--pointers=...] [--bootstrap]`, `mdwiki refresh --bootstrap` | [references/bootstrap.md](references/bootstrap.md) |
| Ingest one source or every pending source through the configured provider | `mdwiki ingest <source>`, `mdwiki ingest --pending [--overview]` | [references/ingest.md](references/ingest.md) |
| Ingest many sources in parallel with native session sub-agents (no API spend) | `mdwiki session-ingest pending \| prepare \| apply` | [references/session-ingest.md](references/session-ingest.md) |
| Find existing pages by exact words, names, or dates without a model | `mdwiki search "<terms>" [--limit N] [--json]` | [references/search.md](references/search.md) |
| Ask a question and get a cited answer, optionally file it | `mdwiki query "<question>" [--file] [--stub]` | [references/query.md](references/query.md) |
| Write a cross-cutting page from what the wiki already knows | `mdwiki synthesize "<topic>" \| --auto` | [references/synthesize.md](references/synthesize.md) |
| Refresh the top-level `wiki/overview.md` | `mdwiki overview` | [references/overview.md](references/overview.md) |
| Check wiki health and repair it | `mdwiki lint [--fix[=full\|=1,3,7]]` | [references/lint.md](references/lint.md) |
| Record or resolve a disagreement between a source and the wiki | plan `contradictions[]`, `mdwiki lint` | [references/contradictions.md](references/contradictions.md) |
| Understand `index.md`, `concept-table.md`, `overview.md`, `sources/`, and page frontmatter | (generated automatically) | [references/navigation.md](references/navigation.md) |
| Roll back the last change(s) | `mdwiki undo [N]` | [references/undo.md](references/undo.md) |

Orientation commands that need no reference: `mdwiki status` (counts, failed sources with reasons, pending contradictions, last lint), `mdwiki source <hash-prefix>` (one source's provenance), `mdwiki doctor` (provider and embedder pre-flight), `mdwiki rebuild [--pages]` (restore `state.db` from `raw/` + `wiki/`).

## Non-negotiables

1. **Cite or refuse.** Every claim written into a page carries a verbatim quote (4+ words by default) from the source being ingested. Quotes that do not anchor reject the whole plan.
2. **Refuse rarely, but refuse.** `low-quality`, `out-of-scope`, and `duplicate-of:<page>` are valid verdicts; topical overlap with an existing page is not grounds for refusal.
3. **Search before you create.** Run `mdwiki search` with the subject's name before proposing a new page; update the existing page if one exists.
4. **Cross-reference every new page.** A page with no inbound or outbound links is an orphan and a lint finding.
5. **Preserve the source's language** in page prose; keep YAML keys, kind values, filenames, and headings in English.
6. **Do not strip frontmatter.** `previous_hash`, `sources`, `tags`, `updated` are load-bearing.

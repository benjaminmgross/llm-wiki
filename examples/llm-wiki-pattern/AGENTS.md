# Agent instructions for this mdwiki folder

This folder is an mdwiki wiki (Karpathy llm-wiki pattern): immutable sources in `raw/`, LLM-maintained pages in `wiki/`, rules in `.mdwiki/schema.md`.

Before any operation here, run `mdwiki skill` and read `.mdwiki/schema.md`. They define this wiki's page kinds, citation rules, and the workflow for each intent.

- Never write to `raw/`. Never hand-edit `wiki/index.md`, `wiki/concept-table.md`, `wiki/log.md`, or `wiki/sources/`; mdwiki regenerates them.
- Search before creating a page: `mdwiki search "<name or term>"`.
- Every claim needs a verbatim source quote; refusing a source is allowed.

Trigger phrases: "ingest this", "what does the wiki say about ...", "lint the wiki".

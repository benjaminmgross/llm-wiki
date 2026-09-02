# Navigation pages, source pages, and frontmatter

mdwiki regenerates these inside every transaction; never hand-edit them.

- `wiki/index.md` — catalog of every page grouped by kind with one-line summaries. The model's entry point for query and synthesize.
- `wiki/concept-table.md` — one row per page: kind, distinct source count, related pages (outgoing links), status, last update. Status is `unsourced`, `single-source`, `multi-source`, or `contradicted` (has a pending contradiction). Built from `state.db` with no model call.
- `wiki/overview.md` — model-written top-level synthesis; refresh with `mdwiki overview`.
- `wiki/sources/<id>-<slug>.md` — one generated page per ingested source: verdict, rationale, pages it fed, and every anchored quote. Provenance in page form for Obsidian and Dataview; `mdwiki source <id>` prints the same from the database.
- `wiki/log.md` — append-only event log; `mdwiki rebuild-log` regenerates it.

## Frontmatter on every page

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

`sources` is the union of every source that has fed the page. `tags` starts as the kind and keeps anything you add. `updated` changes on every write; `created` never does. `previous_hash` is the tamper-evident version chain and is computed on the body only, so frontmatter edits do not break it. Works as-is in Obsidian (relative links) and Dataview (`type`, `sources`, `tags`).

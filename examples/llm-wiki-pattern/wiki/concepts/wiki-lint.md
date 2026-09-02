---
title: Wiki lint
type: concept
created: 2026-09-02
updated: 2026-09-02
sources: [9bed4df13978]
tags: [concept]
---
# Wiki lint

Deterministic health checks for a model-maintained wiki, the maintenance half of the [llm-wiki pattern](llm-wiki-pattern.md).

## Why

A model-maintained wiki drifts: pages go stale when sources change, links break when pages are renamed, and near-duplicate pages appear when two sources describe one idea.

## Rules

Broken references, orphan pages with no inbound links, stale pages whose sources changed, coverage gaps where a source produced nothing, and unresolved contradictions between a source and a page. Contradictions complement [cite or refuse](cite-or-refuse.md): the quote proves what the source says, lint tracks whether the wiki has caught up.

[^src1]: "A model-maintained wiki drifts" (notes/03-maintenance-and-lint.md)
[^src2]: "orphan pages with no inbound links" (notes/03-maintenance-and-lint.md)

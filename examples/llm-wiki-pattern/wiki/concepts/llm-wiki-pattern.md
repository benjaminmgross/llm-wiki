---
title: The llm-wiki pattern
type: concept
created: 2026-09-02
updated: 2026-09-02
sources: [42b213b91efe]
tags: [concept]
previous_hash: c2787ec7412f40a5d22801ff379b62a5751d1817961bb49fad106a1240e1877d
---
# The llm-wiki pattern

A folder of immutable raw sources next to a model-maintained wiki. The wiki is a persistent, compounding artifact: new sources are woven into existing pages rather than filed as standalone summaries.

## Three layers

1. Raw sources, never edited after registration.
2. Wiki pages, owned by the model and rewritten as sources change.
3. A human-written schema that decides page kinds and when the model may create, update, or refuse.

## Guardrails

Pages stay trustworthy because of [cite or refuse](cite-or-refuse.md): every claim quotes its source verbatim, and the model may decline a source instead of force-fitting it. Health is checked by [wiki lint](wiki-lint.md).

The pattern originates in [Karpathy's llm-wiki gist](../entities/karpathy-llm-wiki-gist.md).

[^src1]: "The wiki is a persistent, compounding artifact" (notes/01-llm-wiki-pattern.md)
[^src2]: "Every claim written into a wiki page must quote its source verbatim" (notes/02-cite-or-refuse.md)

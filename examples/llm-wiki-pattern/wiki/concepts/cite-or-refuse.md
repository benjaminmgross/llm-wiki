---
title: Cite or refuse
type: concept
created: 2026-09-02
updated: 2026-09-02
sources: [42b213b91efe, 9bed4df13978]
tags: [concept]
previous_hash: d0a15cf11a53e557f15b9ed1637363fda2a8d67360c421bf1b01e2d124b0fcf0
---
# Cite or refuse

The verification contract behind the [llm-wiki pattern](llm-wiki-pattern.md).

## The rule

Every claim written into a wiki page must quote its source verbatim; a plan with a paraphrased or fabricated quote is rejected as a whole before any file is written.

## Refusal

The model has explicit license to refuse a source. Refusals (`low-quality`, `out-of-scope`) are recorded so the source is not treated as a coverage gap.

## Quote length

Quotes shorter than four words are rejected because a three-word span anchors almost anywhere in a long document.

[^src1]: "rejected as a whole before any file is written" (notes/02-cite-or-refuse.md)
[^src2]: "The model has explicit license to refuse a source" (notes/02-cite-or-refuse.md)
[^src3]: "Quotes shorter than four words are rejected" (notes/02-cite-or-refuse.md)

<!-- mdwiki:cross-refs -->
## Related

- [wiki lint](wiki-lint.md)
<!-- /mdwiki:cross-refs -->

<!-- mdwiki:contradictions -->
## Contradictions

- [pending] wiki: Quotes shorter than four words are rejected. | source: Some implementations set the minimum quote length at five words. | via: 9bed4df13978
<!-- /mdwiki:contradictions -->

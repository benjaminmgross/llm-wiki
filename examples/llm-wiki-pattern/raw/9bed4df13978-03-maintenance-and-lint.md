# Maintenance and lint

## Why lint exists

A model-maintained wiki drifts: pages go stale when sources change, links break when pages are renamed, and near-duplicate pages appear when two sources describe one idea. A lint pass finds these problems deterministically so the human decides what to fix.

## Rules worth having

Useful lint rules include broken references, orphan pages with no inbound links, stale pages whose sources changed, coverage gaps where a source produced nothing, and unresolved contradictions between a source and a page.

## A disagreement

Some implementations argue that the minimum quote length is five words rather than four, trading a little recall for fewer false anchors in dense technical prose.

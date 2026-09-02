# Contradictions

When a source disagrees with what a page already says, do not overwrite silently and do not drop the new claim. Add an entry to the plan's `contradictions[]`:

```json
{
  "page": "wiki/entities/acme.md",
  "existing_claim": "Acme was founded in 2019.",
  "source_claim": "Acme was founded in 2017.",
  "resolution": "pending",
  "claims": [{"source_section_id": "notes.md/History", "quote": "Acme was founded in 2017"}]
}
```

- `page` must be an existing semantic page or one written in the same plan.
- `claims` anchor the *source* side with verbatim quotes, exactly like page claims.
- `resolution` is `pending` (default), `source-wins`, `existing-wins`, or `both-hold`. Only set a non-pending resolution when the source itself justifies it (a newer date, an explicit correction).

mdwiki appends a managed `<!-- mdwiki:contradictions -->` block to the page listing each unresolved disagreement with its source, and records a row in `state.db`. `mdwiki status` shows the pending count; `mdwiki lint` reports `unresolved-contradiction` once a pending entry has outlived `[lint].unresolved_contradiction_after` later ingests (default 3). Resolve by ingesting a source that settles it (its plan sets `resolution`) or by editing the page and re-ingesting.

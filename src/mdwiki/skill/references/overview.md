# Overview page

```bash
mdwiki overview                       # write or refresh wiki/overview.md
mdwiki ingest --pending --overview    # refresh once after a batch
mdwiki refresh --bootstrap --overview
```

`wiki/overview.md` is the top-level synthesis of the whole wiki: what the corpus is about, the main clusters, the open contradictions, and where to start reading. It is produced by the provider from `index.md` and `concept-table.md`, written in a transaction with frontmatter, and excluded from lint's semantic-page rules. Refresh it when an ingest changes the big picture, not after every source.

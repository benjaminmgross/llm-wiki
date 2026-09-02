# Lexical search (no model, no embedder)

```bash
mdwiki search "attention sinks"              # AND of terms, bm25-ranked, top 10
mdwiki search "Maldonado" --limit 3
mdwiki search "2024-11 roadmap" --json       # machine-readable: path, heading, snippet, score
```

`search` queries an FTS5 index over every semantic page's sections stored in `.mdwiki/state.db`. It complements embeddings: embeddings catch paraphrase, FTS catches exact names, identifiers, dates, and rare terms. It is the candidate finder for sub-agents in `session-ingest`, and the check every agent runs before proposing a new page.

Terms are matched as whole tokens with prefix expansion on the last term. Quote a phrase to require adjacency: `mdwiki search '"first principles"'`.

The index is refreshed inside every transaction and after `undo`; `mdwiki rebuild --pages` rebuilds it from disk. If `search` reports an empty index while pages exist, run `mdwiki rebuild --pages`.

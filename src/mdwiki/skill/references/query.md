# Query the wiki

```bash
mdwiki query "what did we conclude about attention sinks?"
mdwiki query "compare A and B" --file        # file the answer as wiki/syntheses/<slug>.md
mdwiki query "does the wiki cover X?" --stub # if not, create a stub page tagged stub, needs-sources
```

The answer is drawn only from existing pages: `index.md` plus the top embedding matches. Every factual statement cites a page with a relative link, and the answer ends with `Confidence: high|medium|low — <reason>` (high = multiple corroborating pages, medium = one page, low = inference).

When the answer is file-worthy (a comparison, a new connection, or a synthesis across three or more pages) and `--file` was not passed, the CLI suggests re-running with `--file`. Filed answers are written in a transaction, get frontmatter, appear in `index.md`, and are logged as a `query-filed` event so `status` distinguishes them from ingests.

When the wiki cannot answer, the model says so (`NO_COVERAGE:`); with `--stub` mdwiki writes a stub concept page with `tags: [stub, needs-sources]` so the gap is visible in `concept-table.md` and lint.

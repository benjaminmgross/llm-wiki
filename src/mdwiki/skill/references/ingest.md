# Ingest sources

```bash
mdwiki ingest <source-id-or-path>      # interactive: prompt → plan → quote-verify → confirm → apply
mdwiki ingest <source> --yes           # skip confirmation
mdwiki ingest --pending [--overview]   # every pending or failed source, resumable
mdwiki ingest --all                    # re-ingest everything, including already-ingested sources
```

## What one ingest does

1. Reads the source from `raw/`, chunks it into sections, embeds the sections locally.
2. Finds candidate pages by embedding similarity (and you can add lexical candidates with `mdwiki search`).
3. Asks the provider for one JSON plan: `verdict`, `rationale`, `updates[]`, `new_pages[]`, `cross_refs[]`, `contradictions[]`.
4. Verifies every quote against the source in pure Python. Fabricated, paraphrased, or joined quotes reject the plan; the provider gets a bounded number of corrective retries.
5. Applies the plan in one transaction: page writes with frontmatter, cross-reference links, contradiction blocks, `wiki/sources/<id>-<slug>.md`, `index.md`, `concept-table.md`, `log.md`, and an undo snapshot.

## Plan rules the model must follow

- `updates[].content` is the COMPLETE revised page, never a diff.
- `new_pages[].kind` must be one of the wiki's allowed kinds; `source` is reserved for generated pages.
- Search before creating: if `mdwiki search "<name>"` finds a page, update it.
- Every new page needs at least one cross-reference.
- When the source disagrees with an existing page, add a `contradictions[]` entry instead of silently overwriting (see [contradictions.md](contradictions.md)).
- Write prose in the source's language; keep keys, kinds, filenames, and headings in English.

## Verdicts

`ingest`, `low-quality`, `out-of-scope`, `duplicate-of:<wiki/page.md>`. Non-ingest verdicts must come with empty arrays; they are recorded so lint does not report a coverage gap.

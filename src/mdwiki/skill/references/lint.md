# Lint and repair

```bash
mdwiki lint                 # report grouped by severity, findings numbered
mdwiki lint --fix           # deterministic fixes only (broken-ref), one prompt per finding
mdwiki lint --fix=full      # also re-ingest stale and coverage-gap sources (provider round-trips)
mdwiki lint --fix=1,3,7     # apply only the numbered findings from the last report
mdwiki lint --fix --yes     # no prompts
```

## Rules

| Kind | Severity | Meaning |
|---|---|---|
| `broken-ref` | warn | a local link whose target does not exist |
| `unverified-quote` | warn | page contains `[unverified-quote]` (lenient quote mode) |
| `unresolved-contradiction` | warn | a `pending` contradiction older than N later ingests |
| `orphan` | info | no other page links here (syntheses and sources exempt) |
| `stale` | info | a cited source was modified after the page was last touched |
| `coverage-gap` | info | an ingested source produced no backrefs and no bounded refusal |
| `duplicate-candidate` | info | two same-kind pages with embedding cosine ≥ `[lint].duplicate_similarity` |
| `isolated-cluster` | info | a group of ≥ 2 pages linked to each other but disconnected from the main graph |

Orphans, duplicates, and isolated clusters need judgment: merge with an `update` in the next ingest, link them from a synthesis (`mdwiki query --file`), or leave them. Thresholds live under `[lint]` in `.mdwiki/config.toml`.

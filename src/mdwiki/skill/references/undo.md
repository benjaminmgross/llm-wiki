# Undo

```bash
mdwiki undo        # roll back the last applied transaction
mdwiki undo 3      # roll back the last three, newest first
```

Every ingest, filed query, synthesis, overview, lint fix, and session apply is one transaction with a file snapshot under `.mdwiki/undo/<tx>/` and inverse SQL rows. Undo restores page files (including generated navigation and source pages), reverses `pages`, `backrefs`, `contradictions`, and source status, rebuilds the search index, and appends an `[undo]` line to `log.md`. Undone sources return to `pending` and are picked up by the next `--pending` run.

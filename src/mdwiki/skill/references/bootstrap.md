# Bootstrap a wiki

## Initialize

```bash
cd <folder>
mdwiki init --profile=working-dir      # profiles: working-dir | initiative | transcripts | framework | research
mdwiki status                          # "N pending sources"
mdwiki doctor                          # provider + API ping + embedder
```

`init` scaffolds `.mdwiki/{config.toml,schema.md,state.db,.gitignore}`, `raw/`, and `wiki/`, registers every loadable file (md, txt, code, csv, pdf, docx, html; images and scanned PDFs are opt-in) as `pending`, and writes runtime pointer files so agents find their instructions:

- `--pointers=claude,codex` (default) writes `CLAUDE.md` and `AGENTS.md`.
- `--pointers=claude,codex,copilot` also writes `.github/copilot-instructions.md`.
- `--pointers=none` writes nothing. Existing files are never overwritten; files `init` writes are excluded from source registration.

Pick the profile by corpus shape; the profile bakes `schema.md` and a config overlay at init time and is not authoritative afterwards.

## Ingest everything

```bash
mdwiki init --bootstrap                # init + `ingest --pending --yes` through the configured provider
mdwiki init --bootstrap-batch -y       # native batch when the provider supports it, explicit sync fallback otherwise
mdwiki refresh --bootstrap [--overview]  # later: register new files and ingest them; cron-friendly
```

Each source commits independently. A failure leaves that source `failed` with a reason in `mdwiki status`; the next `--pending` or bootstrap run retries only pending or failed sources. Add `--overview` to refresh `wiki/overview.md` once at the end of the batch.

If the active session can delegate to sub-agents and you want to avoid API spend, use [session-ingest.md](session-ingest.md) instead of `--bootstrap`.

## After bootstrap

```bash
mdwiki lint                            # health report grouped by severity
mdwiki overview                        # top-level synthesis page
mdwiki query "what does this corpus say about X?"
```

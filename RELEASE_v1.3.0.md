# mdwiki v1.3.0

Release-line notes for the `mdwiki` CLI.

> Current-state correction (2026-10-07): the checkout retains version 1.3.0,
> while [unreleased changes](RELEASES.md) add the session CLI provider,
> environment key lookup, and configured-provider vision during registration.
> Native `session-ingest` is a separate existing workflow: the active session
> prepares plans without provider or local-embedding calls. Current bootstrap
> honors provider capabilities, cross-references become Markdown links, and
> lint recognizes valid intentional refusals. The notes below retain the
> earlier release context; [README.md](README.md) is the operating reference.

> The currentness release. `mdwiki` can now refresh an existing wiki for newly
> added files, keep the source sidecar stable across refreshes and rebuilds, and
> operate more reliably on real folders that change over time. This release also
> carries forward the v1.2 profile system and v1.1 multi-filetype/provider work,
> making `mdwiki` a practical folder-local knowledge base for initiatives,
> transcripts, frameworks, and general working directories.

---

## Highlights

- **Refresh existing wikis**: `mdwiki refresh` re-scans a wiki folder for newly
  added loadable files and registers them as pending sources.
- **One-command upkeep**: `mdwiki refresh --bootstrap` and
  `mdwiki refresh --bootstrap-batch` combine discovery with ingest.
- **Source sidecar merge fix**: refresh now merges into `raw/.sources.json`
  instead of overwriting it, so `mdwiki rebuild` continues to work correctly.
- **Corpus-aware profiles**: `working-dir`, `initiative`, `transcripts`, and
  `framework` profiles seed tuned schemas and page kinds.
- **More robust chunking**: markdown chunking falls back from H2 to H1,
  paragraph, and sliding-window strategies when a source has sparse structure.
- **Agent self-orientation**: `mdwiki skill` prints the active schema plus an
  agent guide from inside any wiki.
- **Quality primitives**: rejection memory, cost ledger tables, quote markers,
  and lint checks give future ingest loops better operational footing.
- **Canary runner**: `scripts/run_canary.py` exercises a real folder and emits
  structural scorecards for page counts, cross-ref density, coverage, and
  backrefs.

## Refresh and Currentness

`mdwiki init` registers the files present when a wiki is created. Before v1.3.0,
new files added later required more manual handling. `mdwiki refresh` closes that
gap:

```bash
mdwiki refresh
mdwiki refresh --bootstrap
mdwiki refresh --bootstrap-batch -y
```

Refresh is content-hash deduplicated. Previously registered files are skipped,
new loadable files become pending, and empty loader outputs are reported.

This makes scheduled maintenance straightforward:

```bash
0 7 * * * cd ~/notes/research && /path/to/mdwiki refresh --bootstrap
```

## Source Sidecar Stability

`raw/.sources.json` is the durable mapping from source hashes to original-path
metadata. v1.3.0 fixes refresh behavior so new registrations merge into the
existing sidecar instead of replacing it.

Impact:

- Rebuilds remain reliable after incremental refreshes.
- Existing source metadata survives new-source discovery.
- Content-addressed raw files continue to work as the immutable source layer.

## Corpus-Aware Profiles

The profile system lets a wiki start with rules that match its corpus:

| Profile | Best for | Page kinds |
| --- | --- | --- |
| `working-dir` | Generic notes and mixed folders | `entity`, `concept`, `synthesis` |
| `initiative` | Projects, strategy, cross-functional work | `decision`, `status`, `workstream`, `owner` |
| `transcripts` | Meetings, calls, interviews | `meeting`, `decision`, `commitment`, `blocker` |
| `framework` | Procedures, templates, assessments | `procedure`, `template`, `assessment`, `learning` |

The profile is baked in at init time. After initialization, users can edit
`.mdwiki/schema.md` directly.

## Ingest Reliability and Hallucination Guardrails

The core ingest loop remains the reliability center of the project:

1. Load source text from `raw/`.
2. Chunk with structure-aware fallbacks.
3. Embed sections locally.
4. Retrieve relevant wiki pages for context.
5. Ask the LLM for a structured plan.
6. Verify every quoted claim against the raw source.
7. Apply accepted changes in a transaction.

The LLM can refuse weak sources as `low-quality`, `out-of-scope`, or
`duplicate-of:<page>` instead of inventing relevance.

## Multi-Filetype Sources

The v1.1 loader registry is part of the current v1.3.0 release line:

| Source type | Support |
| --- | --- |
| Markdown | Native passthrough |
| Text/log/RST | Preserved as fenced text |
| Code | 35+ common language extensions |
| CSV/TSV | Converted to markdown tables |
| PDF | Text extraction via `pypdfium2` |
| DOCX | Headings, paragraphs, tables |
| HTML | Markdown conversion with scripts/styles stripped |
| Images | Opt-in vision descriptions and OCR |

Scanned PDFs and images remain opt-in because they use vision model calls.

## Providers, Batch, and Cost Controls

`mdwiki` supports:

- Anthropic models for normal ingest, vision, and batch ingest.
- OpenAI-compatible providers for vLLM, llama.cpp, OpenRouter, Together, and
  similar endpoints.
- Anthropic Batch API for cheaper first-ingest and refresh-ingest workflows.
- Local embeddings through `sentence-transformers`.
- Cost guard primitives for daily budget enforcement.

## Lint, Recovery, and Auditability

`mdwiki lint` checks structural health:

- Broken local markdown references.
- Orphan pages.
- Stale pages.
- Coverage gaps.
- `[unverified-quote]` markers.

Repair modes:

```bash
mdwiki lint --fix
mdwiki lint --fix=full
```

Operational recovery tools include:

- `mdwiki undo`
- `mdwiki rebuild`
- `mdwiki rebuild-log`
- `mdwiki source <hash-prefix>`
- `mdwiki status`

Every page write can embed a `previous_hash` chain, giving wiki pages a
tamper-evident history.

## Developer and Operator Experience

This release line adds several operator-oriented improvements:

- `mdwiki skill` for agent self-orientation.
- `mdwiki status` summaries for source and page state.
- Canary runner scorecards for real-folder regression checks.
- Cleaner refresh/rebuild behavior for scheduled wiki maintenance.

## Upgrade Notes

Existing v1.0, v1.1, and v1.2 wikis should continue to work with the v1.3.0 CLI.

Recommended after upgrade:

```bash
mdwiki status
mdwiki refresh
mdwiki lint
```

For folders that receive new files regularly, prefer a scheduled
`refresh --bootstrap` or `refresh --bootstrap-batch` workflow.

## Known Limitations

- Native batch is implemented by the Anthropic provider. `--bootstrap-batch`
  falls back explicitly to synchronous ingest through the configured provider
  when native batch is unavailable, including OpenAI-compatible and session
  providers; it never silently substitutes Anthropic.
- Vision OCR is disabled by default for cost control.
- `mdwiki lint --fix=full` can trigger many LLM calls on large stale or
  coverage-gap backlogs.
- Orphans are not auto-fixed because good cross-links require editorial judgment.
- Graph visualization, typed relationship exports, and discovery workflows are
  roadmap items rather than current release features.

## Related Docs

- [README.md](README.md)
- [ROADMAP.md](ROADMAP.md)
- [docs/mdwiki-design.md](docs/mdwiki-design.md)
- [CHANGES_GUIDE.md](CHANGES_GUIDE.md)

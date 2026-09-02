# Native-session multi-agent ingest

Use this when the active Codex or Claude Code session can delegate to sub-agents and a corpus has many pending sources. mdwiki never spawns agents or calls a model here; the session entitlement does the planning and mdwiki does deterministic validation and apply.

## Protocol

1. **Parent:** `mdwiki session-ingest pending` prints a JSON manifest of pending/failed sources. Assign each source to exactly one sub-agent; never duplicate an assignment. Bound parallelism to the smaller of the source count and the session's actual agent-slot limit.
2. **Worker (read-only):** `mdwiki session-ingest prepare <source> -o .mdwiki/session-plans/<source-id>.json`. The envelope contains the source text and sections, the schema, recent log lines, current page paths and hashes, `wiki.lexical_candidates` (pages found by `mdwiki search` for this source's headings), the exact plan JSON contract, and a null `plan`. The worker reads candidate pages, runs `mdwiki search` for any named subject, and fills only `plan`. Workers never write `wiki/`, `state.db`, `raw/.sources.json`, or `log.md`, and never call separate model APIs or local inference.
3. **Parent:** `mdwiki session-ingest apply <envelope>.json`, one at a time. Apply re-verifies quotes, takes the wiki write lock, re-checks source/config/schema hashes and every target page's hash, then runs the normal transaction.
4. **Exit code 3 = invalidated.** Something the plan touched changed after preparation. Re-run `prepare` for that source, re-plan with a fresh sub-agent, retry; cap at three retries, then leave the source pending/failed and continue.
5. **Resume** an interrupted run from `session-ingest pending`; already-ingested sources are absent.

Keep envelopes under `.mdwiki/` so `refresh` never registers them as sources. Session-applied pages carry no embedding until `mdwiki rebuild --pages` or a provider-backed ingest refreshes the cache; the FTS search index is maintained regardless.

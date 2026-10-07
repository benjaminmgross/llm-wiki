# llm-wiki

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

`llm-wiki` ships `mdwiki`: a folder-local CLI that turns a directory of mixed
sources into an LLM-maintained wiki.

Built on [Karpathy's llm-wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f),
`mdwiki` keeps sources immutable, writes wiki pages as a compounding artifact,
and answers questions with citations back to the source-backed wiki. You provide
the folder and curate the result; the LLM does the bookkeeping.

## What It Does

`mdwiki` creates a self-contained knowledge base next to your files:

- `raw/` stores content-addressed, immutable source copies.
- `wiki/` stores LLM-maintained markdown pages.
- `.mdwiki/` stores schema, config, local SQLite state, and undo data.

Each ingest reads a source, retrieves relevant existing pages, asks the LLM for a
structured update plan, verifies quoted evidence, and applies the changes inside
an undoable transaction.

## Key Capabilities

- **Folder-local wikis**: move or copy the folder and the wiki goes with it.
- **Mixed source ingest**: markdown, text, code, CSV/TSV, PDF, DOCX, HTML, and
  opt-in image OCR.
- **Corpus-aware profiles**: seed schemas for generic working directories,
  initiatives, transcripts, and frameworks.
- **Quote verification**: every LLM-generated citation is checked against the raw
  source before it is applied.
- **Cited Q&A**: ask questions over existing wiki pages and get cited answers.
- **Synthesis pages**: generate cross-cutting writeups from topics or graph
  clusters.
- **Health checks**: lint broken refs, orphans, stale pages, coverage gaps, and
  unverified quote markers.
- **Recovery tools**: undo transactions, rebuild state, and regenerate logs.
- **Provider flexibility**: Anthropic by default, plus OpenAI-compatible
  endpoints for local or hosted inference.
- **Native-session multi-agent corpus ingest**: an active Codex or Claude Code
  session can assign unique sources to read-only sub-agents, then validate and
  apply their plans serially without provider or local-inference calls.
- **Session mode**: run ingest, query, synthesize and OCR through a local agent
  CLI, with subscription login checks for Claude Code and Codex by default.
- **Provider-aware bootstrap**: `init/refresh --bootstrap-batch` uses native
  batch when the configured provider supports it, otherwise an explicit
  synchronous fallback through that same provider. It never silently switches
  to Anthropic.
- **Durable partial failures**: failed sources retain their reason in SQLite and
  `raw/.sources.json`, appear in `mdwiki status`, and are retried without
  reingesting successful sources.

## Install

```bash
git clone <this repo>
cd llm-wiki
uv sync
# Only when provider = "anthropic":
export ANTHROPIC_API_KEY=sk-ant-...
```

The CLI binary lands at `.venv/bin/mdwiki`:

```bash
.venv/bin/mdwiki --help
```

Optional shell alias:

```bash
alias mdwiki=~/path/to/llm-wiki/.venv/bin/mdwiki
```

## Quickstart

Point `mdwiki` at any folder of sources:

```bash
cd ~/notes/research
mdwiki init --profile=working-dir
mdwiki status
mdwiki doctor
mdwiki ingest --pending              # includes previously failed sources
mdwiki session-ingest pending        # manifest for native session sub-agent assignment
mdwiki query "what did I conclude about transformer attention sinks?"
mdwiki synthesize "fine-tuning vs RAG decision framework"
mdwiki lint
```

Bootstrap a wiki in one step:

```bash
mdwiki init --bootstrap
mdwiki init --profile=initiative --bootstrap
```

Prefer native batch for larger first ingests. Providers without native batch
support print a notice and use the same configured provider synchronously:

```bash
mdwiki init --profile=initiative --bootstrap-batch -y
```

## Wiki Layout

After `mdwiki init`, the target folder looks like this:

```text
your-folder/
  .mdwiki/
    config.toml          # provider, model, embedder, loader settings
    schema.md            # page kinds, naming rules, citation rules
    state.db             # local sqlite cache (gitignored)
    write.lock           # sqlite-backed cross-process wiki write lock (gitignored)
    .gitignore           # ignores state.db and undo dirs
  raw/
    .sources.json        # source metadata sidecar
    a3f1b2c4d5e6-foo.md  # content-addressed source copies
  wiki/
    index.md             # generated page catalog
    log.md               # append-only event log
    entities/
    concepts/
    syntheses/
```

The folder is the wiki. `mdwiki` discovers it by walking upward for `.mdwiki/`,
similar to how `git` discovers a repository.

## Core Commands

| Command | Purpose |
| --- | --- |
| `mdwiki init [path] [--profile=<name>] [--bootstrap \| --bootstrap-batch]` | Scaffold `.mdwiki/`, register loadable files, and optionally ingest outstanding pending/failed sources through the configured provider. |
| `mdwiki refresh [path] [--bootstrap \| --bootstrap-batch]` | Re-scan for newly-added files and optionally ingest outstanding pending/failed sources. |
| `mdwiki status` | Show pending/failed/ingested counts, failed-source reasons, page counts, recent events, and last lint. |
| `mdwiki source <hash-prefix>` | Inspect one registered source and its dependent pages. |
| `mdwiki doctor` | Ping the selected provider with a model call and report its model, login result, and configured embedder. |
| `mdwiki ingest <source>` | Ingest one source by path, raw filename, or hash prefix. |
| `mdwiki ingest --pending` | Ingest every pending or failed source. |
| `mdwiki ingest --all` | Re-ingest every source, including already-ingested ones. |
| `mdwiki session-ingest pending` | Emit a stable JSON manifest of pending/failed sources, once each, for parent-session assignment |
| `mdwiki session-ingest prepare <source> [-o envelope.json]` | Capture a read-only source/schema/page-hash envelope and plan contract without calling a provider or embedder |
| `mdwiki session-ingest apply <envelope.json>` | Revalidate a session-produced plan against latest source/schema/target pages and apply one serialized per-source transaction; exit 3 means re-prepare and retry |
| `mdwiki query "<question>" [--file]` | Answer from existing wiki pages; optionally file the answer as a synthesis. |
| `mdwiki synthesize "<topic>" \| --auto` | Create synthesis pages explicitly or from graph clusters. |
| `mdwiki lint [--fix [=full]]` | Check wiki health; optionally apply supported fixes. |
| `mdwiki rebuild` | Reconstruct `.mdwiki/state.db` from `raw/.sources.json` and `wiki/log.md`. |
| `mdwiki rebuild-log` | Regenerate `wiki/log.md` from the events table. |
| `mdwiki undo [N]` | Roll back the last N applied transactions. |
| `mdwiki skill` | Print the wiki schema plus an agent guide for in-folder AI agents. |

## Profiles

Profiles seed a wiki with a schema and config tuned to a corpus type. The profile
is applied at init time; after that, `.mdwiki/schema.md` is yours to edit.

| Profile | Best for | Added page kinds |
| --- | --- | --- |
| `working-dir` | Generic notes and mixed-topic folders | `entity`, `concept`, `synthesis` |
| `initiative` | Project, strategy, and cross-functional folders | `decision`, `status`, `workstream`, `owner` |
| `transcripts` | Meetings, calls, interviews, VTT/SRT/Fathom-style notes | `meeting`, `decision`, `commitment`, `blocker` |
| `framework` | Procedures, templates, assessments, learning corpora | `procedure`, `template`, `assessment`, `learning` |

```bash
mdwiki init --profile=initiative ~/dev/projects/capital-raise
mdwiki init --profile=transcripts ~/recordings/team-meetings
```

Profile source files live under `src/mdwiki/profiles/`.

## Supported Sources

| Extension | Loader | Notes |
| --- | --- | --- |
| `.md`, `.markdown` | `MarkdownLoader` | Passthrough markdown. |
| `.txt`, `.log`, `.rst` | `TextLoader` | Wrapped in a fenced code block. |
| `.py`, `.js`, `.ts`, `.go`, `.rs`, `.java`, `.rb`, `.sh`, `.sql`, ... | `CodeLoader` | Wrapped in a language-tagged fence. |
| `.csv`, `.tsv` | `CsvLoader` | Converted to a markdown table; truncates to 100 rows. |
| `.pdf` | `PdfLoader` | Text via `pypdfium2`; opt-in vision fallback for scanned PDFs. |
| `.docx` | `DocxLoader` | Preserves headings, paragraphs, and tables. |
| `.html`, `.htm` | `HtmlLoader` | Converts to markdown and strips scripts/styles. |
| `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif` | `ImageLoader` | Opt-in only; uses provider vision support. |

## Native-session Multi-agent Corpus Ingest

`session-ingest` is the deterministic handoff for an active Codex or Claude Code session. mdwiki does not spawn agents itself, and this path never calls `mdwiki.llm`, a model SDK/HTTP endpoint, or the local embedder.

1. The parent runs `mdwiki session-ingest pending` and assigns every listed source to exactly one native session sub-agent. Planning concurrency is bounded by the active session's actual agent-slot limit.
2. Each worker stays read-only and runs `mdwiki session-ingest prepare <source> -o .mdwiki/session-plans/<source-id>.json`. Keeping envelopes under `.mdwiki/` prevents a later refresh from registering them as corpus sources. The envelope contains source sections, schema, current page paths/hashes, agent instructions, and the exact plan JSON contract. The worker fills only `plan`.
3. The parent applies completed envelopes one at a time with `mdwiki session-ingest apply <envelope>.json`.
4. Apply acquires the wiki-wide write lock, rechecks source/configuration/schema hashes, and compares every write/read-modify-write page (including a cross-reference `from_page`) with its analysis-time hash. A target-only cross-reference endpoint needs only to continue existing under the lock. Unrelated and target-only content changes do not invalidate a plan; policy changes, overlapping writes, deleted endpoints, and new-page races do.
5. Exit code 3 means the plan was invalidated. The parent re-prepares and re-plans that source, capped at three retries, while continuing other sources. Successful sources remain independently committed and undoable; interrupted or exhausted work remains pending/failed for the next manifest.

Because session plans deliberately avoid local inference, their page rows carry no embedding until a later provider-backed ingest or `mdwiki rebuild --pages` refreshes the derived embedding cache. Page content, citations, relative cross-reference links, transactions, source state, index, log, and undo remain complete.

## Configuration

Each wiki has a local `.mdwiki/config.toml`:

```toml
[llm]
provider = "anthropic"
model = "claude-sonnet-4-6"

[embedder]
model = "sentence-transformers/all-MiniLM-L6-v2"

[ingest]
candidate_top_k = 8
max_undo_history = 50

[loaders.image]
enabled = false

[loaders.pdf]
vision_fallback = false

[exclude]
globs = []
```

Both `init` and `refresh` respect `.gitignore` and `[exclude].globs` during
discovery. Config globs use gitignore-style matching, including negation; they
cannot re-include a path excluded by `.gitignore`.

### OpenAI-Compatible Providers

Use `provider = "openai-compatible"` for endpoints that implement OpenAI's chat
completions API, such as vLLM, llama.cpp, OpenRouter, or Together:

```toml
[llm]
provider = "openai-compatible"
model = "qwen2.5-72b-instruct"

[llm.openai_compatible]
base_url = "http://localhost:8000/v1"
api_key = "not-needed-for-vllm"
timeout = 120.0
vision_capable = false
```

For hosted providers, keep secrets out of tracked config by reading the key from
an environment variable:

```toml
[llm.openai_compatible]
base_url = "https://openrouter.ai/api/v1"
api_key_env = "OPENROUTER_API_KEY"
```

Replace the earlier `api_key` setting when using `api_key_env`: an explicit
`api_key` takes precedence. If the named environment variable is absent or
empty, provider construction fails with a configuration error.

Notes:

- `--bootstrap-batch` honors this provider configuration. Most OpenAI-compatible
  endpoints do not expose native batch, so mdwiki explicitly falls back to
  sequential ingest through this same endpoint; it never substitutes Anthropic.
- Each source commits independently. Failures remain retryable and are listed
  with reasons by `mdwiki status`.
- Image ingest and scanned-PDF OCR require a vision-capable provider.
- Vision loaders are disabled by default; enabling them uses the configured
  provider's inference resources and may incur API charges.

### Session Mode (No API Key)

Use `provider = "session"` to run model-backed commands through a locally
installed agent CLI. The built-in Claude Code and OpenAI Codex adapters check
for a subscription login by default. A `custom` command is also supported, with
authentication and billing controlled by that command.

This provider starts a CLI process per model call and keeps the usual local
embedding retrieval. The separate [native-session workflow](#native-session-multi-agent-corpus-ingest)
uses the active session to produce plans without provider or embedding calls.

```toml
[llm]
provider = "session"

[llm.session]
cli = "claude"              # "claude" | "codex" | "custom"
model = "sonnet"            # optional; codex and custom default to the CLI's own model
timeout = 1500.0            # seconds per call
max_output_tokens = 32000   # claude only
verify_subscription = true  # built-in CLIs only; custom login is not checked
accepted_auth_methods = ["claude.ai"]  # claude only; logins that count as a subscription
strip_env = []              # extra environment variables to remove from the child process
keep_env = []               # exemptions apply to every CLI; may retain API credentials
```

Provider selection follows `--provider` (or the provider implied by
`--session-cli`), then `MDWIKI_PROVIDER`, then `[llm].provider`. CLI selection
follows `--session-cli`, then `MDWIKI_SESSION_CLI`, then `[llm.session].cli`.
Session models use `MDWIKI_SESSION_MODEL`, then `[llm.session].model`; they do
not inherit `[llm].model`. Omit the session model to use `sonnet` for Claude or
the CLI's default for Codex/custom. Remove the example `model = "sonnet"` when
switching to a CLI that does not accept that model name.

Override the configured provider for one invocation, a shell, or a scheduled
job without editing config:

```bash
mdwiki --provider session ingest --pending
mdwiki --provider session --session-cli codex query "what changed last week?"
export MDWIKI_PROVIDER=session MDWIKI_SESSION_CLI=codex MDWIKI_SESSION_MODEL=gpt-5
env -u MDWIKI_SESSION_CLI mdwiki --provider anthropic refresh --bootstrap-batch  # run once against the API
```

A custom command receives the prompt on stdin and must print the completion to
stdout. `{system_file}`, `{model}` and `{image}` are substituted; without
`{system_file}` the system prompt is prepended to the prompt.

```toml
[llm.session]
cli = "custom"
command = ["my-agent", "--model", "{model}", "--system", "{system_file}"]
```

| Command | Session mode |
| --- | --- |
| `ingest`, `ingest --pending`, `lint --fix=full` | Yes; free-form JSON plan with the existing corrective retries |
| `init/refresh --bootstrap` and `--bootstrap-batch` | Yes; sequential ingest (no native batch) |
| `query`, `synthesize`, `doctor` | Yes |
| Scanned-PDF and image OCR | Yes with `claude` and `codex`; `custom` needs an `{image}` placeholder |

`--session-cli` on its own implies `--provider session`. `MDWIKI_SESSION_CLI`
requires the provider to already be `session`, selected through config,
`MDWIKI_PROVIDER`, or `--provider`. Combining `--session-cli` with an
API provider, or exporting `MDWIKI_SESSION_CLI` while the provider is an API
provider, is an error rather than a silent no-op. A wiki created with
`mdwiki --provider session init` is written with `provider = "session"`.
New wikis also retain explicit session CLI/model environment overrides. An
override on an existing wiki lasts only for that invocation and does not rewrite
its config.

Session mode's default safeguards for built-in `claude` and `codex` CLIs:

- Removes from the child process, without regard to case, every variable
  starting with `ANTHROPIC_`, `OPENAI_`, `CODEX_`, `CLAUDE_CODE_`, `AWS_`,
  `GOOGLE_`, `AZURE_` or `VERTEX`, plus `CLAUDECODE` and `CLOUD_ML_REGION`.
  That covers API keys, gateway base URLs and cloud-provider switches.
  `CODEX_HOME`, `CLAUDE_CONFIG_DIR` and `CLAUDE_CODE_OAUTH_TOKEN` are kept,
  because the CLI needs them to find its subscription login. `keep_env`
  exemptions take precedence over removal, including `strip_env`.
- Before the first completion, and before the next call after a session CLI
  failure, checks the
  login. `claude auth status` must report an `authMethod` on the accepted list
  (default `claude.ai`) and no API-key source. `codex login status` must report
  a ChatGPT login. Anything else, including an unrecognized login, is refused.
- Never falls back to another provider.

Limits you should know:

- These checks cannot verify billing outcomes. `verify_subscription = false`
  turns the login check off, a `custom` command's login is never checked, and
  `keep_env` can retain API credentials or gateway settings. CLI-managed
  credentials and organization policy are outside mdwiki's control.
  `mdwiki doctor` makes a model call and reports the selected provider and
  whether its subscription login check ran.
- A login with `CLAUDE_CODE_OAUTH_TOKEN` may report an `authMethod` other than
  `claude.ai`; add it to `accepted_auth_methods` once you have confirmed it is
  a subscription login.
- Missing CLIs, rejected logins, configuration errors, and reported rate limits
  stop a bulk run; unprocessed sources retain their pending or failed status.
  Timeouts and ordinary per-call failures remain source-local, so bulk ingest
  can continue and failed sources can be retried. Timeout or cancellation
  terminates the child process group. CLI token/cost reports are not billing
  receipts.
- Sources are ingested one at a time, so a first ingest of a large folder is
  slower than the Anthropic batch path.
- The per-call output cap that API providers honor is not forwarded;
  `max_output_tokens` applies to every call (claude only).
- Codex runs in a read-only sandbox inside an empty temporary directory with
  its shell and image-file reading tools disabled. Images are passed directly
  as attachments. Unsupported tool-disable switches fail before completion;
  mdwiki never retries with these restrictions removed. Claude runs with no tools, except
  during image transcription, when only `Read` is enabled. Claude vision
  requires a CLI whose `--restricted` mode confines file tools to working
  directories; mdwiki checks that contract in `claude --help` and refuses OCR
  if it is unavailable. The image is copied into an isolated temporary working
  directory, and no additional directories are allowed.
- With a vision loader enabled, registration during `init` or `refresh` can
  call the configured provider: once per image, or once per PDF page when text
  extraction for the entire PDF is empty. These calls happen before converted
  content deduplication, so repeated refreshes can repeat OCR. Under an API
  provider they may incur charges. Without vision, registration builds no
  provider and requires no model credentials. If provider construction fails
  because of missing credentials or invalid configuration, registration warns
  and continues without vision. A systemic session error during OCR stops
  discovery after persisting earlier successful registrations in SQLite and
  the sidecar; per-call errors skip the affected file for a later refresh.

## How Ingest Works

1. Load a registered source from `raw/`.
2. Chunk it into sections with fallbacks for sparse structure.
3. Embed sections locally.
4. Retrieve the most relevant existing wiki pages.
5. Ask the LLM for a structured ingest plan.
6. Verify every quoted claim against the raw source.
7. Apply accepted changes inside a SQLite-backed transaction.
8. Update wiki files, source state, page embeddings, backrefs, and logs.

Planned `cross_refs` become relative Markdown links in their source pages, so
readers and `mdwiki lint` observe the same graph. `mdwiki undo` reverses every
page and link edit.

mdwiki delegates CommonMark interpretation—including inline and reference-style
links, titles, escapes, and code fences—to
[`markdown-it-py`](https://markdown-it-py.readthedocs.io/). Its own
cross-reference code is limited to wiki path policy and byte-preserving edits
inside `<!-- mdwiki:cross-refs -->` blocks. This boundary is intentional:
standardized syntax belongs to a maintained parser library, while mdwiki owns
only its domain-specific semantics. The same parser supplies exact source forms
for deterministic broken-link fixes. A fix is applied only when that source
form occurs uniquely, so identical examples in code spans or fences fail closed
instead of being rewritten. Transactional writes preserve explicit LF/CRLF
content and existing frontmatter newline style.

The LLM has explicit license to refuse: a source can verdict `low-quality`, `out-of-scope`, or `duplicate-of:<page>` instead of being force-fit into the wiki. Because an intentional refusal correctly produces no page backrefs, `mdwiki lint` does not report it as a coverage gap when the source's latest durable rejection or ingest event records one of those bounded verdicts. Matching is case-sensitive, `duplicate-of:` requires a non-whitespace page, and ingest events must match the complete production summary envelope (`<verdict>: <original_path> — <rationale>`). An ingest event wins when cross-store timestamps tie. An arbitrary verdict, a malformed summary, an older superseded refusal, or an ingested source with zero backrefs and no bounded refusal remains a coverage-gap finding.

All ingest transactions reserve a separate sqlite-backed wiki write lock before any file snapshot or replacement and hold it through database commit, sidecar mirroring, and log append. This protects page files, `index.md`, `state.db`, `raw/.sources.json`, and `log.md` across concurrent processes.

## Keeping a Wiki Current

`init` registers files that exist at initialization time. Use `refresh` to pick up
new files later:

```bash
mdwiki refresh
mdwiki refresh --bootstrap
mdwiki refresh --bootstrap-batch -y
```

Refresh is content-hash deduplicated. It adds newly discovered sources; it does
not remove old sources or pages.

For scheduled maintenance, run refresh from inside the wiki folder:

```bash
0 7 * * * cd ~/notes/research && /path/to/mdwiki refresh --bootstrap
```

## Lint and Repair

`mdwiki lint` checks:

- `broken-ref`: markdown links to missing local pages.
- `orphan`: pages with no inbound wiki links.
- `stale`: pages whose cited sources changed after the page was last touched.
- `coverage-gap`: ingested sources with no backrefs, except valid intentional
  refusals recorded by the latest durable rejection or ingest event.
- `unverified-quote`: pages containing `[unverified-quote]`.

Fix modes:

```bash
mdwiki lint --fix       # deterministic broken-ref cleanup
mdwiki lint --fix=full  # also re-ingests stale and coverage-gap sources
```

Orphans require editorial judgment and are intentionally not auto-fixed.

## Recovery

Every applied transaction records enough information for rollback:

```bash
mdwiki undo
mdwiki undo 3
```

If local state is lost or a log needs repair:

```bash
mdwiki rebuild
mdwiki rebuild-log
```

## Documentation

- [Release notes](RELEASES.md)
- [Roadmap](ROADMAP.md)
- [Architecture](docs/mdwiki-design.md)
- [Contributing](CONTRIBUTING.md)
- [Historical changes](CHANGES_GUIDE.md)

## License

MIT

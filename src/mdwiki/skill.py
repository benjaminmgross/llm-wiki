"""Runtime ``mdwiki skill`` — print the wiki's schema + an agent how-to guide.

Borrows the pattern from llmwiki-cli's ``wiki skill`` command (research doc):
let a Claude Code (or other AI agent) instance opening a wiki folder
self-orient with a single command. Prints ``.mdwiki/schema.md`` followed by
a short "How to use this wiki" guide.
"""

from __future__ import annotations

from pathlib import Path

from mdwiki.discover import WIKI_DIR_NAME

AGENT_GUIDE: str = """\
# How to use this wiki

If you are a Claude Code (or other AI agent) instance working in this folder,
this is your runbook for the mdwiki CLI.

## What you have available

The wiki layer is LLM-maintained. Sources live in ``raw/`` (immutable,
content-addressed). Pages live in ``wiki/`` (LLM-owned). The schema above
governs what page kinds exist, when to update vs. create, and how to cite.

## Common operations

- ``mdwiki status`` — pending/failed/ingested counts, failed reasons, last lint, recent events
- ``mdwiki ingest <source>`` — interactive single-source ingest (you can ``--yes``)
- ``mdwiki ingest --pending`` — bulk-ingest every pending or failed source
- ``mdwiki query "<question>"`` — cited Q&A from existing wiki pages
- ``mdwiki synthesize "<topic>"`` — produce a synthesis page from existing pages
- ``mdwiki lint`` — broken refs, orphans, stale, coverage gaps; ``--fix`` interactive
- ``mdwiki undo`` — roll back the last applied transaction
- ``mdwiki source <hash-prefix>`` — inspect a registered source
- ``mdwiki history <page>`` (when available) — walk the page's previous_hash chain

## Running through an agent CLI (session mode)

Commands that call a model (``ingest``, ``init/refresh --bootstrap``,
``query``, ``synthesize``, ``lint --fix=full``, ``doctor``) use the wiki's
configured provider. Session mode runs these commands through a local agent
CLI, with subscription login verification for built-in CLIs by default.

- Check which provider this wiki uses: ``mdwiki doctor``
- One invocation: ``mdwiki --provider session ingest --pending``
- Through Codex instead of Claude Code: ``mdwiki --provider session --session-cli codex query "..."``
- Whole shell or scheduled job: ``export MDWIKI_PROVIDER=session``
- Permanently for this wiki: set ``provider = "session"`` under ``[llm]`` in ``.mdwiki/config.toml``

Session mode removes known API credential and gateway environment variables
from the child process and never falls back to another provider. By default,
built-in CLIs must report an accepted subscription login. A ``custom`` command's
login is never checked; ``verify_subscription = false`` disables verification,
and ``keep_env`` can retain credentials or gateway settings. These safeguards
cannot verify billing outcomes. If the CLI reports a usage limit, a bulk run
stops and the remaining sources stay pending. ``mdwiki status``,
``mdwiki lint`` (without ``--fix=full``), ``mdwiki source`` and ``mdwiki undo``
never call a model. ``mdwiki init`` and ``mdwiki refresh`` call one only when a
vision loader is enabled in ``.mdwiki/config.toml``.

## Working with this wiki

1. **Read the schema above first.** It tells you what page kinds exist,
   what's required when creating each, and what the lint policies are.
2. **Cite or refuse.** Every claim you write into a wiki page must include
   a verbatim quote (4+ words) from a source in ``raw/``. Quotes that
   don't anchor are rejected; the entire plan fails.
3. **Empty plans are valid.** When a source has no load-bearing content
   for this wiki, return verdict ``low-quality`` or ``out-of-scope``.
4. **Cross-reference everything you create.** A new page with no
   inbound or outbound links is an orphan and a lint failure.
5. **You can refuse the user's framing.** When their question is
   malformed or unanswerable from the wiki, say so explicitly rather
   than padding an answer.

## Multi-agent corpus ingestion (native session entitlement)

Use this protocol when the active Codex or Claude Code session can delegate to
sub-agents and a corpus has multiple pending sources:

1. Run ``mdwiki session-ingest pending`` once. The parent assigns each listed
   source to exactly one sub-agent; never duplicate an assignment.
2. Bound parallel work to the smaller of the source count and the active
   session's agent-slot limit. Use only native delegation from the active
   Codex or Claude Code session. Workers must never use separate model APIs or local inference.
3. Workers are read-only. For one assigned source, run
   ``mdwiki session-ingest prepare <source> --output .mdwiki/session-plans/<source-id>.json``, inspect
   source/schema/wiki context, and fill only the envelope's ``plan`` field.
   Keeping envelopes under ``.mdwiki/`` prevents refresh from registering them
   as sources. Workers never write ``wiki/``, ``state.db``, ``raw/.sources.json``, or logs.
4. The parent collects envelopes and runs ``mdwiki session-ingest apply`` one
   at a time. Parent-only apply serializes wiki/database writes and preserves
   one transaction and undo record per source.
5. If apply reports an invalidated plan, the parent prepares fresh context and
   must retry that source with a fresh sub-agent plan. Cap this at three retries;
   leave an exhausted source pending/failed and continue the corpus.
6. Resume an interrupted run from ``session-ingest pending``. Already-ingested
   sources are absent, while pending or failed sources remain eligible.

## Page version chain

When you (or any tool) overwrite a page in ``wiki/``, mdwiki automatically
embeds ``previous_hash:`` in the page's YAML frontmatter linking back to
the prior version's body hash. This is tamper-evident: hand-editing a page
outside mdwiki creates a hash mismatch downstream tools can detect.

Don't strip ``previous_hash:`` — it's load-bearing.
"""


class WikiNotFoundForSkill(FileNotFoundError):
    """Raised by ``run_skill`` when invoked outside a wiki folder."""


def run_skill(wiki_root: Path) -> str:
    """Return the wiki's schema followed by the agent guide.

    Parameters
    ----------
    wiki_root : Path
        Directory containing ``.mdwiki/schema.md``.

    Returns
    -------
    str
        Schema body + ``\\n\\n---\\n\\n`` separator + agent guide. Suitable
        for ``print(...)`` directly.

    Raises
    ------
    WikiNotFoundForSkill
        When ``.mdwiki/schema.md`` doesn't exist under ``wiki_root``.
    """
    schema_path = wiki_root / WIKI_DIR_NAME / "schema.md"
    if not schema_path.is_file():
        raise WikiNotFoundForSkill(
            f"No schema at {schema_path}; not a wiki folder. Run `mdwiki init` first."
        )
    schema_text = schema_path.read_text()
    return f"{schema_text.rstrip()}\n\n---\n\n{AGENT_GUIDE}"

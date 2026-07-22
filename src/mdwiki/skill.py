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

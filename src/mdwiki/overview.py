"""``mdwiki overview`` — model-written top-level synthesis of the whole wiki.

Reads ``wiki/index.md`` and ``wiki/concept-table.md`` (both generated), asks
the provider for a short orientation essay, and files it as
``wiki/overview.md`` in a transaction. The page is infrastructure for lint and
plans (never a cross-reference endpoint) but keeps the version chain because a
model wrote it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mdwiki.discover import WIKI_DIR_NAME
from mdwiki.frontmatter import apply_page_metadata
from mdwiki.llm import build_provider_from_config
from mdwiki.llm.anthropic import OutputTruncatedError
from mdwiki.llm.base import Message, Provider
from mdwiki.navigation import CONCEPT_TABLE_PATH, INDEX_PATH, build_concept_table
from mdwiki.prompts import OVERVIEW_SYSTEM_PROMPT, build_overview_user_prompt
from mdwiki.state import connect
from mdwiki.transaction import IngestTransaction

OVERVIEW_PATH: str = "wiki/overview.md"
OVERVIEW_MAX_TOKENS_DEFAULT: int = 6000


class OverviewError(Exception):
    """Raised when the overview cannot be produced (no wiki, truncated output)."""


@dataclass(frozen=True)
class OverviewResult:
    """Outcome of ``generate_overview``."""

    applied: bool
    message: str
    filed_path: str | None


def generate_overview(
    wiki_root: Path,
    *,
    provider: Provider | None = None,
    max_tokens: int = OVERVIEW_MAX_TOKENS_DEFAULT,
) -> OverviewResult:
    """Write or refresh ``wiki/overview.md``. A refusal (``INSUFFICIENT_COVERAGE:``) writes nothing."""
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    if not db_path.is_file():
        raise OverviewError(f"No wiki at {wiki_root}/{WIKI_DIR_NAME}/. Run `mdwiki init` first.")

    schema_text = (wiki_root / WIKI_DIR_NAME / "schema.md").read_text()
    index_path = wiki_root / INDEX_PATH
    index_text = index_path.read_text() if index_path.is_file() else ""
    table_path = wiki_root / CONCEPT_TABLE_PATH
    table_text = table_path.read_text() if table_path.is_file() else build_concept_table(wiki_root)
    with connect(db_path) as conn:
        pending = conn.execute(
            "SELECT page_path, existing_claim, source_claim FROM contradictions WHERE resolution = 'pending' ORDER BY page_path, id"
        ).fetchall()
    contradictions = [
        f'{row["page_path"]}: wiki says "{row["existing_claim"]}" but a source says "{row["source_claim"]}"' for row in pending
    ]

    provider = provider or build_provider_from_config(wiki_root)
    try:
        response = provider.complete(
            system=OVERVIEW_SYSTEM_PROMPT,
            messages=[
                Message(
                    role="user",
                    content=build_overview_user_prompt(
                        schema_text=schema_text,
                        index_text=index_text,
                        concept_table_text=table_text,
                        pending_contradictions=contradictions,
                    ),
                )
            ],
            max_tokens=max_tokens,
        )
    except OutputTruncatedError as exc:
        raise OverviewError(str(exc)) from exc

    body = response.text.strip()
    if body.startswith("INSUFFICIENT_COVERAGE:"):
        return OverviewResult(applied=False, message=body, filed_path=None)
    if not body:
        return OverviewResult(applied=False, message="provider returned an empty overview", filed_path=None)

    page = apply_page_metadata(body if body.endswith("\n") else body + "\n", path=OVERVIEW_PATH, kind="overview", source_ids=())
    with IngestTransaction(
        wiki_root=wiki_root, source_id=None, summary="overview: refreshed wiki/overview.md", event_kind="overview"
    ) as tx:
        tx.write_file(wiki_root / OVERVIEW_PATH, page)
    return OverviewResult(applied=True, message=f"overview written: {OVERVIEW_PATH}", filed_path=OVERVIEW_PATH)

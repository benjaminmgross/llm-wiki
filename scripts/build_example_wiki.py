"""Rebuild ``examples/llm-wiki-pattern/`` deterministically — no provider, no embedder.

The example is a real mdwiki wiki: three short notes about the llm-wiki pattern
are registered with ``init_wiki``, then each is applied through the
``session-ingest`` path with a hand-written plan. That exercises quote
verification, transactions, cross-reference and contradiction materialization,
generated source pages, frontmatter, and navigation exactly as a live ingest
would, so the committed output doubles as documentation and as a lint fixture
(``tests/test_example_wiki.py``).

Usage::

    uv run python scripts/build_example_wiki.py

The sqlite cache, write lock, and undo snapshots are removed afterwards; they
are gitignored and ``mdwiki rebuild`` + ``mdwiki rebuild --pages`` restore them.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from mdwiki.init import init_wiki
from mdwiki.session_ingest import apply_session_plan, prepare_session_plan

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = REPO_ROOT / "examples" / "llm-wiki-pattern"

SOURCES: dict[str, str] = {
    "notes/01-llm-wiki-pattern.md": """# The llm-wiki pattern

## Idea

An llm-wiki is a folder of immutable raw sources next to a wiki that a language model maintains on your behalf. The wiki is a persistent, compounding artifact: each new source is woven into existing pages instead of being filed as a standalone summary.

## Three layers

The pattern has three layers. Raw sources are never edited after they are registered. Wiki pages are owned by the model and rewritten whenever a source changes what they should say. A schema document written by the human decides which page kinds exist and when the model may create, update, or refuse.

## Origin

The pattern was described in Andrej Karpathy's llm-wiki gist in 2026, which framed the wiki as the model's long-term memory for a corpus.
""",
    "notes/02-cite-or-refuse.md": """# Cite or refuse

## The rule

Every claim written into a wiki page must quote its source verbatim. A plan that contains a paraphrased or fabricated quote is rejected as a whole before any file is written.

## Refusal is allowed

The model has explicit license to refuse a source. A source with nothing citable receives the verdict low-quality or out-of-scope, and the refusal is recorded so the source is not treated as a coverage gap.

## Quote length

Quotes shorter than four words are rejected because a three-word span anchors almost anywhere in a long document.
""",
    "notes/03-maintenance-and-lint.md": """# Maintenance and lint

## Why lint exists

A model-maintained wiki drifts: pages go stale when sources change, links break when pages are renamed, and near-duplicate pages appear when two sources describe one idea. A lint pass finds these problems deterministically so the human decides what to fix.

## Rules worth having

Useful lint rules include broken references, orphan pages with no inbound links, stale pages whose sources changed, coverage gaps where a source produced nothing, and unresolved contradictions between a source and a page.

## A disagreement

Some implementations argue that the minimum quote length is five words rather than four, trading a little recall for fewer false anchors in dense technical prose.
""",
}


def _plan_for_source_1() -> dict[str, object]:
    return {
        "verdict": "ingest",
        "rationale": "Introduces the pattern itself and its origin; both deserve pages.",
        "updates": [],
        "new_pages": [
            {
                "path": "wiki/concepts/llm-wiki-pattern.md",
                "kind": "concept",
                "content": (
                    "# The llm-wiki pattern\n\n"
                    "A folder of immutable raw sources next to a model-maintained wiki. The wiki is a persistent, compounding artifact: new sources are woven into existing pages rather than filed as standalone summaries.\n\n"
                    "## Three layers\n\n"
                    "1. Raw sources, never edited after registration.\n"
                    "2. Wiki pages, owned by the model and rewritten as sources change.\n"
                    "3. A human-written schema that decides page kinds and when the model may create, update, or refuse.\n\n"
                    "The pattern originates in [Karpathy's llm-wiki gist](../entities/karpathy-llm-wiki-gist.md).\n\n"
                    '[^src1]: "The wiki is a persistent, compounding artifact" (notes/01-llm-wiki-pattern.md)\n'
                ),
                "claims": [
                    {"source_section_id": "notes/01-llm-wiki-pattern.md/Idea", "quote": "The wiki is a persistent, compounding artifact"},
                    {
                        "source_section_id": "notes/01-llm-wiki-pattern.md/Three layers",
                        "quote": "Raw sources are never edited after they are registered",
                    },
                ],
            },
            {
                "path": "wiki/entities/karpathy-llm-wiki-gist.md",
                "kind": "entity",
                "content": (
                    "# Karpathy's llm-wiki gist\n\n"
                    "The 2026 gist by Andrej Karpathy that described the [llm-wiki pattern](../concepts/llm-wiki-pattern.md), framing the wiki as the model's long-term memory for a corpus.\n\n"
                    '[^src1]: "framed the wiki as the model\'s long-term memory for a corpus" (notes/01-llm-wiki-pattern.md)\n'
                ),
                "claims": [
                    {
                        "source_section_id": "notes/01-llm-wiki-pattern.md/Origin",
                        "quote": "framed the wiki as the model's long-term memory for a corpus",
                    },
                ],
            },
        ],
        "cross_refs": [
            {
                "from_page": "wiki/concepts/llm-wiki-pattern.md",
                "to_page": "wiki/entities/karpathy-llm-wiki-gist.md",
                "anchor_text": "Karpathy's llm-wiki gist",
            },
        ],
    }


def _plan_for_source_2() -> dict[str, object]:
    return {
        "verdict": "ingest",
        "rationale": "Defines the verification rule the pattern relies on; extends the pattern page and adds a concept page.",
        "updates": [
            {
                "page": "wiki/concepts/llm-wiki-pattern.md",
                "content": (
                    "# The llm-wiki pattern\n\n"
                    "A folder of immutable raw sources next to a model-maintained wiki. The wiki is a persistent, compounding artifact: new sources are woven into existing pages rather than filed as standalone summaries.\n\n"
                    "## Three layers\n\n"
                    "1. Raw sources, never edited after registration.\n"
                    "2. Wiki pages, owned by the model and rewritten as sources change.\n"
                    "3. A human-written schema that decides page kinds and when the model may create, update, or refuse.\n\n"
                    "## Guardrails\n\n"
                    "Pages stay trustworthy because of [cite or refuse](cite-or-refuse.md): every claim quotes its source verbatim, and the model may decline a source instead of force-fitting it. Health is checked by [wiki lint](wiki-lint.md).\n\n"
                    "The pattern originates in [Karpathy's llm-wiki gist](../entities/karpathy-llm-wiki-gist.md).\n\n"
                    '[^src1]: "The wiki is a persistent, compounding artifact" (notes/01-llm-wiki-pattern.md)\n'
                    '[^src2]: "Every claim written into a wiki page must quote its source verbatim" (notes/02-cite-or-refuse.md)\n'
                ),
                "claims": [
                    {
                        "source_section_id": "notes/02-cite-or-refuse.md/The rule",
                        "quote": "Every claim written into a wiki page must quote its source verbatim",
                    },
                ],
            }
        ],
        "new_pages": [
            {
                "path": "wiki/concepts/cite-or-refuse.md",
                "kind": "concept",
                "content": (
                    "# Cite or refuse\n\n"
                    "The verification contract behind the [llm-wiki pattern](llm-wiki-pattern.md).\n\n"
                    "## The rule\n\n"
                    "Every claim written into a wiki page must quote its source verbatim; a plan with a paraphrased or fabricated quote is rejected as a whole before any file is written.\n\n"
                    "## Refusal\n\n"
                    "The model has explicit license to refuse a source. Refusals (`low-quality`, `out-of-scope`) are recorded so the source is not treated as a coverage gap.\n\n"
                    "## Quote length\n\n"
                    "Quotes shorter than four words are rejected because a three-word span anchors almost anywhere in a long document.\n\n"
                    '[^src1]: "rejected as a whole before any file is written" (notes/02-cite-or-refuse.md)\n'
                    '[^src2]: "The model has explicit license to refuse a source" (notes/02-cite-or-refuse.md)\n'
                    '[^src3]: "Quotes shorter than four words are rejected" (notes/02-cite-or-refuse.md)\n'
                ),
                "claims": [
                    {"source_section_id": "notes/02-cite-or-refuse.md/The rule", "quote": "rejected as a whole before any file is written"},
                    {
                        "source_section_id": "notes/02-cite-or-refuse.md/Refusal is allowed",
                        "quote": "The model has explicit license to refuse a source",
                    },
                    {
                        "source_section_id": "notes/02-cite-or-refuse.md/Quote length",
                        "quote": "Quotes shorter than four words are rejected",
                    },
                ],
            }
        ],
        "cross_refs": [],
    }


def _plan_for_source_3() -> dict[str, object]:
    return {
        "verdict": "ingest",
        "rationale": "Adds the maintenance side of the pattern and records a disagreement about the minimum quote length.",
        "updates": [],
        "new_pages": [
            {
                "path": "wiki/concepts/wiki-lint.md",
                "kind": "concept",
                "content": (
                    "# Wiki lint\n\n"
                    "Deterministic health checks for a model-maintained wiki, the maintenance half of the [llm-wiki pattern](llm-wiki-pattern.md).\n\n"
                    "## Why\n\n"
                    "A model-maintained wiki drifts: pages go stale when sources change, links break when pages are renamed, and near-duplicate pages appear when two sources describe one idea.\n\n"
                    "## Rules\n\n"
                    "Broken references, orphan pages with no inbound links, stale pages whose sources changed, coverage gaps where a source produced nothing, and unresolved contradictions between a source and a page. Contradictions complement [cite or refuse](cite-or-refuse.md): the quote proves what the source says, lint tracks whether the wiki has caught up.\n\n"
                    '[^src1]: "A model-maintained wiki drifts" (notes/03-maintenance-and-lint.md)\n'
                    '[^src2]: "orphan pages with no inbound links" (notes/03-maintenance-and-lint.md)\n'
                ),
                "claims": [
                    {"source_section_id": "notes/03-maintenance-and-lint.md/Why lint exists", "quote": "A model-maintained wiki drifts"},
                    {
                        "source_section_id": "notes/03-maintenance-and-lint.md/Rules worth having",
                        "quote": "orphan pages with no inbound links",
                    },
                ],
            }
        ],
        "cross_refs": [
            {"from_page": "wiki/concepts/cite-or-refuse.md", "to_page": "wiki/concepts/wiki-lint.md", "anchor_text": "wiki lint"},
        ],
        "contradictions": [
            {
                "page": "wiki/concepts/cite-or-refuse.md",
                "existing_claim": "Quotes shorter than four words are rejected.",
                "source_claim": "Some implementations set the minimum quote length at five words.",
                "resolution": "pending",
                "claims": [
                    {
                        "source_section_id": "notes/03-maintenance-and-lint.md/A disagreement",
                        "quote": "the minimum quote length is five words rather than four",
                    },
                ],
            }
        ],
    }


def build(example_dir: Path = EXAMPLE_DIR) -> Path:
    if example_dir.exists():
        shutil.rmtree(example_dir)
    for rel, text in SOURCES.items():
        target = example_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    result = init_wiki(example_dir, profile="working-dir")
    assert result.files_registered == len(SOURCES), result.message

    plans = {
        "notes/01-llm-wiki-pattern.md": _plan_for_source_1(),
        "notes/02-cite-or-refuse.md": _plan_for_source_2(),
        "notes/03-maintenance-and-lint.md": _plan_for_source_3(),
    }
    for original_path, plan in plans.items():
        envelope = prepare_session_plan(example_dir, original_path).to_dict()
        applied = apply_session_plan(example_dir, {**envelope, "plan": plan})
        print(applied.message)

    for name in ("state.db", "state.db-wal", "state.db-shm", "state.db-journal", "write.lock"):
        path = example_dir / ".mdwiki" / name
        if path.exists():
            path.unlink()
    for folder in ("undo", "session-plans"):
        path = example_dir / ".mdwiki" / folder
        if path.exists():
            shutil.rmtree(path)
    return example_dir


if __name__ == "__main__":
    built = build()
    print(f"example wiki rebuilt at {built}")

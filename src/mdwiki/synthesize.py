"""Channel B for synthesis pages — topic-mode + auto-mode (cluster discovery).

Topic mode is the explicit twin of ``mdwiki query --file``: the user says
*"write a synthesis on X"*, the LLM produces it (or refuses), the page lands
in ``wiki/syntheses/``. Auto mode walks the cross-reference graph, finds
clusters of mutually-linking pages, and asks the LLM whether each cluster
warrants a synthesis page.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mdwiki.discover import WIKI_DIR_NAME
from mdwiki.embedder import Embedder, get_default_embedder
from mdwiki.embeddings import deserialize, find_top_k, serialize
from mdwiki.frontmatter import apply_page_metadata
from mdwiki.llm import build_provider_from_config
from mdwiki.llm.anthropic import OutputTruncatedError
from mdwiki.llm.base import Message, Provider
from mdwiki.prompts import (
    SYNTHESIZE_CLUSTER_SYSTEM_PROMPT,
    SYNTHESIZE_TOPIC_SYSTEM_PROMPT,
    build_cluster_user_prompt,
    build_synthesize_topic_user_prompt,
)
from mdwiki.query import slugify_question
from mdwiki.state import connect
from mdwiki.transaction import IngestTransaction

TOPIC_CANDIDATES_TOP_K: int = 8
TOPIC_MAX_TOKENS_DEFAULT: int = 8000
CLUSTER_MAX_TOKENS_DEFAULT: int = 1000
CLUSTER_MIN_SIZE: int = 3


class SynthesisError(Exception):
    """Raised when synthesize cannot proceed."""


@dataclass(frozen=True)
class SynthesisResult:
    """Outcome of one ``synthesize_topic`` call (or one cluster in auto mode)."""

    topic_or_title: str
    applied: bool
    message: str
    filed_path: str | None


def synthesize_topic(
    wiki_root: Path,
    topic: str,
    *,
    yes: bool = False,
    confirm: Callable[[str], bool] | None = None,
    provider: Provider | None = None,
    embedder: Embedder | None = None,
    max_tokens: int = TOPIC_MAX_TOKENS_DEFAULT,
) -> SynthesisResult:
    """Generate a synthesis page on ``topic`` from the wiki's most-relevant pages.

    Raises
    ------
    SynthesisError
        If no wiki is reachable or the LLM truncates mid-output.
    """
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    if not db_path.is_file():
        raise SynthesisError(f"No wiki at {wiki_root}/{WIKI_DIR_NAME}/. Run `mdwiki init` first.")

    schema_text = (wiki_root / WIKI_DIR_NAME / "schema.md").read_text()
    index_path = wiki_root / "wiki" / "index.md"
    index_text = index_path.read_text() if index_path.is_file() else ""

    embedder = embedder or get_default_embedder()
    topic_vec = embedder.embed_text(topic)
    candidate_pages = _find_candidate_pages(wiki_root=wiki_root, query_vec=topic_vec)

    user_prompt = build_synthesize_topic_user_prompt(
        topic=topic,
        candidate_pages=candidate_pages,
        schema_text=schema_text,
        index_text=index_text,
    )

    provider = provider or build_provider_from_config(wiki_root)
    try:
        response = provider.complete(
            system=SYNTHESIZE_TOPIC_SYSTEM_PROMPT,
            messages=[Message(role="user", content=user_prompt)],
            max_tokens=max_tokens,
        )
    except OutputTruncatedError as exc:
        raise SynthesisError(str(exc)) from exc

    body = response.text.strip()
    refusal = _detect_refusal(body)
    if refusal is not None:
        return SynthesisResult(topic_or_title=topic, applied=False, message=refusal, filed_path=None)

    if not yes:
        chooser = confirm or _terminal_confirm
        if not chooser(body):
            return SynthesisResult(topic_or_title=topic, applied=False, message="rejected by user", filed_path=None)

    filed_path = _file_synthesis(wiki_root=wiki_root, topic=topic, body=body, embedder=embedder)
    return SynthesisResult(
        topic_or_title=topic,
        applied=True,
        message=f"synthesized: {filed_path}",
        filed_path=filed_path,
    )


def synthesize_auto(
    wiki_root: Path,
    *,
    yes: bool = False,
    confirm_each: Callable[[SynthesisResult], bool] | None = None,
    provider: Provider | None = None,
    embedder: Embedder | None = None,
    max_proposals: int = 5,
    cluster_max_tokens: int = CLUSTER_MAX_TOKENS_DEFAULT,
    page_max_tokens: int = TOPIC_MAX_TOKENS_DEFAULT,
) -> list[SynthesisResult]:
    """Discover cross-ref clusters and propose a synthesis for each.

    Returns one ``SynthesisResult`` per cluster (whether or not it became a page).
    """
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    if not db_path.is_file():
        raise SynthesisError(f"No wiki at {wiki_root}/{WIKI_DIR_NAME}/. Run `mdwiki init` first.")

    graph = build_cross_ref_graph(wiki_root)
    clusters = find_clusters(graph, min_size=CLUSTER_MIN_SIZE)
    clusters = clusters[:max_proposals]

    if not clusters:
        return []

    provider = provider or build_provider_from_config(wiki_root)
    embedder = embedder or get_default_embedder()
    results: list[SynthesisResult] = []

    for cluster in clusters:
        cluster_pages = _read_pages(wiki_root, sorted(cluster))
        try:
            cluster_response = provider.complete(
                system=SYNTHESIZE_CLUSTER_SYSTEM_PROMPT,
                messages=[Message(role="user", content=build_cluster_user_prompt(cluster_pages=cluster_pages))],
                max_tokens=cluster_max_tokens,
            )
        except OutputTruncatedError as exc:
            results.append(SynthesisResult(topic_or_title="<cluster>", applied=False, message=str(exc), filed_path=None))
            continue

        verdict = _parse_cluster_verdict(cluster_response.text)
        if verdict is None or not verdict.get("propose"):
            rationale = (verdict or {}).get("rationale", "no synthesis warranted")
            results.append(
                SynthesisResult(
                    topic_or_title="<cluster>",
                    applied=False,
                    message=f"no-synthesis: {rationale}",
                    filed_path=None,
                )
            )
            continue

        title = verdict.get("title") or "untitled-synthesis"
        proposal = SynthesisResult(topic_or_title=title, applied=False, message="proposed", filed_path=None)
        if not yes:
            chooser = confirm_each or _terminal_confirm_proposal
            if not chooser(proposal):
                results.append(SynthesisResult(topic_or_title=title, applied=False, message="rejected by user", filed_path=None))
                continue

        per_cluster_result = synthesize_topic(
            wiki_root,
            title,
            yes=True,
            provider=provider,
            embedder=embedder,
            max_tokens=page_max_tokens,
        )
        results.append(per_cluster_result)

    return results


# --- Cross-ref graph + clustering ----------------------------------------------

_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+\.md)\)")


def build_cross_ref_graph(wiki_root: Path) -> dict[str, set[str]]:
    """Return adjacency: each wiki page → set of wiki pages it links to."""
    wiki_dir = wiki_root / "wiki"
    graph: dict[str, set[str]] = {}
    if not wiki_dir.is_dir():
        return graph

    for page_path in wiki_dir.rglob("*.md"):
        if page_path.name in {"index.md", "log.md"}:
            continue
        rel = page_path.relative_to(wiki_root).as_posix()
        targets: set[str] = set()
        for match in _LINK_RE.finditer(page_path.read_text()):
            link = match.group(1)
            normalized = _normalize_link(link, from_dir=page_path.parent.relative_to(wiki_root))
            if normalized != rel and (wiki_root / normalized).is_file():
                targets.add(normalized)
        graph[rel] = targets
    return graph


def _normalize_link(link: str, *, from_dir: Path) -> str:
    """Resolve ``link`` (relative to ``from_dir``) into a wiki-root-relative posix path."""
    if link.startswith("wiki/"):
        return link
    parts = (from_dir / link).parts
    out: list[str] = []
    for part in parts:
        if part == "..":
            if out:
                out.pop()
        elif part not in (".", ""):
            out.append(part)
    return "/".join(out)


def find_clusters(graph: dict[str, set[str]], *, min_size: int = 3) -> list[set[str]]:
    """Return connected components of the undirected version of ``graph`` with size ≥ ``min_size``."""
    nodes: set[str] = set(graph)
    for neighbours in graph.values():
        nodes.update(neighbours)

    undirected: dict[str, set[str]] = {n: set() for n in nodes}
    for src, targets in graph.items():
        for tgt in targets:
            undirected[src].add(tgt)
            undirected.setdefault(tgt, set()).add(src)

    seen: set[str] = set()
    components: list[set[str]] = []
    for node in nodes:
        if node in seen:
            continue
        component: set[str] = set()
        stack = [node]
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            component.add(current)
            stack.extend(undirected.get(current, set()) - seen)
        if len(component) >= min_size:
            components.append(component)
    return components


# --- Helpers shared with query.py-style behaviour -------------------------------

def _find_candidate_pages(*, wiki_root: Path, query_vec: list[float]) -> list[dict[str, str]]:
    db_path = wiki_root / WIKI_DIR_NAME / "state.db"
    with connect(db_path) as conn:
        rows = conn.execute("SELECT path, embedding FROM pages WHERE embedding IS NOT NULL").fetchall()
    if not rows:
        return []
    page_vectors = {row["path"]: deserialize(row["embedding"]) for row in rows}
    ranked = find_top_k(query_vec, page_vectors, k=TOPIC_CANDIDATES_TOP_K, min_similarity=0.0)
    return [
        {"path": path, "content": (wiki_root / path).read_text()}
        for path, _score in ranked
        if (wiki_root / path).is_file()
    ]


def _read_pages(wiki_root: Path, paths: list[str]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for path in paths:
        full = wiki_root / path
        if full.is_file():
            out.append({"path": path, "content": full.read_text()})
    return out


def _detect_refusal(body: str) -> str | None:
    """Return a refusal message if ``body`` starts with one of the agreed prefixes, else None."""
    for prefix in ("INSUFFICIENT_COVERAGE:", "DUPLICATE_OF:"):
        if body.startswith(prefix):
            return body.strip()
    return None


def _parse_cluster_verdict(text: str) -> dict | None:
    """Strip optional fences/preamble, parse JSON. Return None on any failure."""
    text = text.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1 :]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[: -len("```")]
    first = text.find("{")
    last = text.rfind("}")
    if first == -1 or last == -1:
        return None
    try:
        return json.loads(text[first : last + 1])
    except json.JSONDecodeError:
        return None


def _file_synthesis(*, wiki_root: Path, topic: str, body: str, embedder: Embedder) -> str:
    slug = slugify_question(topic)
    rel_path = f"wiki/syntheses/{slug}.md"
    target = wiki_root / rel_path
    summary = f"synthesize topic: {topic[:80]}"

    body = apply_page_metadata(body if body.endswith("\n") else body + "\n", path=rel_path, kind="synthesis", source_ids=())
    with IngestTransaction(wiki_root=wiki_root, source_id=None, summary=summary) as tx:
        tx.write_file(target, body)
        embedding_blob = serialize(embedder.embed_text(body))
        tx.upsert_page(path=rel_path, kind="synthesis", embedding=embedding_blob, last_touched_at=_now_ts())
    return rel_path


def _terminal_confirm(body: str) -> bool:
    print(f"\n--- proposed synthesis page ---\n\n{body}\n\n--- end ---\n")
    return input("Apply this synthesis page? [y/N] ").strip().lower() == "y"


def _terminal_confirm_proposal(proposal: SynthesisResult) -> bool:
    print(f"\nProposed cluster synthesis: {proposal.topic_or_title}")
    return input("Generate this synthesis page? [y/N] ").strip().lower() == "y"


def _now_ts() -> float:
    import time

    return time.time()



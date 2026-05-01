"""Scaffold a new ``.mdwiki/`` wiki and register every markdown source as ``pending``.

The starting state after ``init_wiki`` is intentionally empty:
``raw/`` holds content-addressed copies, ``wiki/`` holds nothing yet, and ``state.db``
records every source as ``pending`` ingest. The user picks how to bootstrap from there.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pathspec
import tomli_w

from mdwiki.discover import WIKI_DIR_NAME, WikiNotFound, find_wiki
from mdwiki.loaders import UnsupportedFiletypeError, build_registry
from mdwiki.loaders.base import Loader
from mdwiki.state import connect, init_db

SOURCES_SIDECAR_NAME: str = ".sources.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "llm": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
    },
    "embedder": {
        "model": "sentence-transformers/all-MiniLM-L6-v2",
    },
    "ingest": {
        "candidate_top_k": 8,
        "max_undo_history": 50,
    },
    "exclude": {
        "globs": [],
    },
    # Loader-specific opt-ins (Phase 5). Both default to false because vision calls
    # cost ~$0.10–0.50 each — users opt in explicitly per wiki by editing this file.
    "loaders": {
        "image": {"enabled": False},
        "pdf": {"vision_fallback": False},
    },
}

DEFAULT_SCHEMA: str = """\
# mdwiki schema

The conventions and policies the LLM follows when ingesting sources, writing pages,
and answering queries. Edit this file to steer your wiki's structure.

## Page kinds

| Kind | Folder | Purpose |
|---|---|---|
| `entity` | `wiki/entities/` | A specific person, place, project, paper, or system |
| `concept` | `wiki/concepts/` | An idea, technique, or pattern discussed across many sources |
| `synthesis` | `wiki/syntheses/` | A cross-cutting writeup that braids several concepts together |

## Naming

- Entities and concepts: lowercase-kebab-case, one topic per page (`attention-sinks.md`)
- Syntheses: short noun phrases (`transformer-attention-survey.md`)
- Always link to other pages with relative markdown links (`[attention sinks](../concepts/attention-sinks.md)`)

## Citation format

Every claim in `wiki/` must be supported by a quote from one or more sources in `raw/`.
The LLM appends a footnote-style citation block at the bottom of each page section:

```
[^src1]: <quote>... (raw/<hash>-<slug>.md, original: <original_path>)
```

## When to update, create entity pages, or refuse

**Touch breadth target: 5–15 wiki pages per source ingest** — typically a few updates, 1–3 new pages (most often entities), and 3–8 cross-refs. If your plan touches ≤2 pages, you are almost certainly under-creating; re-read the source and ask which named subjects deserve their own pages.

Three actions you must consider for every source — they are NOT mutually exclusive:

1. **Update existing concept / synthesis pages** when the source extends or refines a concept the wiki already covers. Don't duplicate concepts (don't create both `concepts/first-principles.md` and `concepts/first-principles-thinking.md` — pick one).

2. **Create entity pages — REQUIRED for named subjects.** When a source has 2+ substantive factual claims about a SPECIFIC named person, project, paper, system, or organization, create or update an entity page for them. This is how the wiki avoids becoming a handful of bloated concept pages. Examples:
   - Source mentions Aristotle in passing → no entity page; cite him in the concept page.
   - Source describes Aristotle's archai, Metaphysics, lineage from Plato → CREATE `wiki/entities/aristotle.md`.
   - Source describes SpaceX's vertical integration, Falcon 1 timeline, Merlin engine → CREATE `wiki/entities/spacex.md`.
   - Heuristic: if you can write 3+ sentences about a named subject from the source, that subject earns an entity page.

3. **Create concept pages** ONLY when the source introduces a NEW concept with no existing home. New concept pages are the rarest of the three — most overlap is handled by updates + new entity pages, not new concepts.

## When to refuse — RARELY

The `low-quality` and `out-of-scope` verdicts are reserved for sources that genuinely contain no load-bearing content for THIS wiki. Topical overlap with an existing concept page is NOT grounds for refusal — it is the common case for related sources, and it warrants UPDATES + NEW ENTITY PAGES, not rejection.

Refuse only when:
- The source has zero factual claims a wiki page could cite (e.g. a utility script with no novel logic).
- The source's subject matter is wholly outside the wiki's domain.

Do NOT refuse when:
- A primary text (e.g. Aristotle's *Metaphysics*) overlaps with existing summaries — the entity page for that thinker should still be enriched, and direct quotes from the primary should replace paraphrased citations where possible.
- An academic paper's core thesis is mentioned in passing on another page — create an entity page for the paper, update the related concepts.

## Cross-references — REQUIRED ON CREATION

**Every new page must include at least one cross-reference to a related existing page.** A new page with no inbound or outbound links is an orphan and a lint failure waiting to happen.

If a candidate-pages list is provided, your new pages should link to whichever of those candidates is conceptually adjacent — even if you didn't update them. Use markdown relative links: `[concept name](../concepts/concept-name.md)`.

## Synthesis pages — TRIGGER CONDITIONS

Propose a `synthesis` page when:
- A new source ties together ≥3 existing concept or entity pages, OR
- You notice ≥3 existing pages would benefit from a unified writeup that compares/contrasts them

Synthesis pages are short cross-cutting essays (300–800 words) with heavy cross-reference density, not catalog pages.

## Lint policies

- Pages with no inbound `cross_refs` are flagged as orphans
- Pages whose source files were modified after `last_touched_at` are flagged as stale
- Pages with header depth > 4 are flagged for restructuring
- Sources with backref count of 0 after ingest are flagged as poorly absorbed
- Broken cross-references (markdown link to a path that doesn't exist) are flagged
"""

GITIGNORE_CONTENT: str = """\
# mdwiki local cache (rebuildable from raw/ + wiki/ + log.md)
state.db
state.db-journal
state.db-wal
state.db-shm
undo/
"""


class NestedWikiError(Exception):
    """Raised when ``init_wiki`` is called inside a folder whose parent already has a wiki."""


@dataclass(frozen=True)
class InitResult:
    """The outcome of an ``init_wiki`` call.

    Parameters
    ----------
    created : bool
        True if this call scaffolded a new wiki; False if one already existed.
    files_registered : int
        Number of sources copied to ``raw/`` and inserted into ``sources``.
    files_skipped : int
        Number of files that were skipped (e.g. matched a ``.gitignore``).
    dedup_skipped : int
        Number of files whose content matched an already-registered source
        (same SHA-256). The first occurrence wins; subsequent copies are
        skipped without overwriting the existing ``raw/`` file.
    empty_load_skipped : int
        Number of files for which the matched loader returned an empty string
        (e.g. ``PdfLoader`` on a scanned/image-only PDF). No raw file is
        written and no DB row is inserted; the loader logs a warning naming
        the path before the skip.
    wiki_root : Path
        The directory containing the new (or pre-existing) ``.mdwiki/``.
    message : str
        A short human-readable summary; CLI prints this verbatim.
    """

    created: bool
    files_registered: int
    files_skipped: int
    dedup_skipped: int
    empty_load_skipped: int
    wiki_root: Path
    message: str


def init_wiki(target: Path) -> InitResult:
    """Scaffold a wiki at ``target`` and register every markdown source as pending.

    Parameters
    ----------
    target : Path
        The folder to turn into a wiki. Must not be inside another wiki.

    Returns
    -------
    InitResult
        See class docstring.

    Raises
    ------
    NestedWikiError
        If a parent of ``target`` already contains ``.mdwiki/``.
    """
    target = target.resolve()
    _refuse_if_nested(target)

    wiki_dir = target / WIKI_DIR_NAME
    if wiki_dir.is_dir():
        return InitResult(
            created=False,
            files_registered=0,
            files_skipped=0,
            dedup_skipped=0,
            empty_load_skipped=0,
            wiki_root=target,
            message=f"Already initialized at {target}/{WIKI_DIR_NAME}/. No changes.",
        )

    raw_dir = target / "raw"
    inner_wiki_dir = target / "wiki"
    wiki_dir.mkdir()
    raw_dir.mkdir(exist_ok=True)
    inner_wiki_dir.mkdir(exist_ok=True)

    init_db(wiki_dir / "state.db")
    (wiki_dir / "config.toml").write_bytes(tomli_w.dumps(DEFAULT_CONFIG).encode("utf-8"))
    (wiki_dir / "schema.md").write_text(DEFAULT_SCHEMA)
    (wiki_dir / ".gitignore").write_text(GITIGNORE_CONTENT)

    # Build the loader registry ONCE from the default config. Reusing this
    # avoids the O(N) TOML re-parse cost (one parse + ancestor walk per file)
    # the previous per-file ``get_loader_for`` path incurred. Image loading is
    # off by default so no provider is needed here; init never invokes vision.
    registry = build_registry(config=DEFAULT_CONFIG, provider=None)

    registered, skipped, dedup_skipped, empty_load_skipped = _register_sources(
        target=target, raw_dir=raw_dir, db_path=wiki_dir / "state.db", registry=registry
    )

    suffix_parts: list[str] = []
    if skipped:
        suffix_parts.append(f"skipped {skipped} via .gitignore")
    if dedup_skipped:
        suffix_parts.append(f"skipped {dedup_skipped} duplicate-content")
    if empty_load_skipped:
        suffix_parts.append(f"skipped {empty_load_skipped} with empty loader output")
    suffix = f" ({'; '.join(suffix_parts)})." if suffix_parts else "."

    return InitResult(
        created=True,
        files_registered=registered,
        files_skipped=skipped,
        dedup_skipped=dedup_skipped,
        empty_load_skipped=empty_load_skipped,
        wiki_root=target,
        message=(
            f"Initialized wiki at {target}/{WIKI_DIR_NAME}/. "
            f"Registered {registered} source(s) as pending" + suffix
        ),
    )


def _refuse_if_nested(target: Path) -> None:
    """Raise ``NestedWikiError`` if any parent of ``target`` already contains ``.mdwiki/``."""
    if target.parent == target:
        return
    try:
        ancestor = find_wiki(target.parent)
    except WikiNotFound:
        return
    raise NestedWikiError(
        f"Cannot init at {target}: parent wiki found at {ancestor}/{WIKI_DIR_NAME}/. "
        "mdwiki only supports a single wiki per folder tree (like git)."
    )


def _register_sources(
    *,
    target: Path,
    raw_dir: Path,
    db_path: Path,
    registry: tuple[Loader, ...],
) -> tuple[int, int, int, int]:
    """Walk ``target``, register every loadable file as a pending source.

    Parameters
    ----------
    registry : tuple[Loader, ...]
        Pre-built loader registry. Required — every file's loader resolution
        uses this in-memory registry rather than calling ``get_loader_for``,
        which would re-parse ``.mdwiki/config.toml`` per file (O(N) TOML
        parses for a corpus of N files).

    Returns
    -------
    tuple[int, int, int, int]
        ``(registered, gitignore_skipped, dedup_skipped, empty_load_skipped)``.

    Duplicate-content files (same SHA-256 prefix → same primary key) are
    skipped via ``INSERT OR IGNORE``; the first occurrence wins, the existing
    ``raw/<hash>-<slug>.md`` is left untouched, and a counter is bumped. Files
    whose loader returns an empty string (scanned PDFs, empty CSVs, etc.) are
    counted in ``empty_load_skipped`` — no raw write, no DB row. Any other
    per-row failure is logged to stderr and skipped, so one bad file can't
    abort the whole walk.
    """
    import sys

    spec = _load_gitignore(target)
    source_paths = sorted(_iter_loadable_files(target, registry=registry))
    registered = 0
    skipped = 0
    dedup_skipped = 0
    empty_load_skipped = 0
    sidecar: dict[str, dict[str, str | float]] = {}

    with connect(db_path) as conn:
        for source_path in source_paths:
            try:
                rel_posix = source_path.relative_to(target).as_posix()
                if spec is not None and spec.match_file(rel_posix):
                    skipped += 1
                    continue

                # Hash original bytes — the source's identity. Loader output is
                # derived; if a loader changes (bug fix, format tweak), the same
                # source still resolves to the same id.
                content_bytes = source_path.read_bytes()
                content_hash = hashlib.sha256(content_bytes).hexdigest()
                short_hash = content_hash[:12]

                # Pre-check by primary key: if another file with identical
                # content has already been registered this run, skip BOTH the
                # INSERT and the loader call. Without this pre-check, two files
                # sharing content but differing in stem produce two different
                # raw/<hash>-<slug>.md filenames; the second copy lands on
                # disk before INSERT OR IGNORE drops the row, leaving an
                # orphan file with no DB row and no sidecar entry.
                already_registered = conn.execute(
                    "SELECT 1 FROM sources WHERE id = ?", (short_hash,)
                ).fetchone()
                if already_registered is not None:
                    dedup_skipped += 1
                    continue

                loader = _resolve_loader(source_path, registry=registry)
                markdown_text = loader.load_to_markdown(source_path)
                if not markdown_text.strip():
                    # Loader returned empty (e.g. PdfLoader on a scanned PDF,
                    # CsvLoader on an empty CSV). The loader has already logged
                    # a warning naming the path; surface the count via
                    # InitResult.empty_load_skipped so the CLI summary shows it.
                    empty_load_skipped += 1
                    continue

                slug = _slugify(source_path.stem)
                raw_filename = f"{short_hash}-{slug}.md"
                raw_path_abs = raw_dir / raw_filename
                # Don't clobber an existing raw/ file — the first registration
                # wins both in the DB and on disk. raw/ is a homogeneous
                # markdown store regardless of original filetype.
                if not raw_path_abs.exists():
                    raw_path_abs.write_text(markdown_text)
                mtime = source_path.stat().st_mtime

                conn.execute(
                    "INSERT INTO sources (id, original_path, raw_path, content_hash, mtime, status) VALUES (?, ?, ?, ?, ?, ?)",
                    (short_hash, rel_posix, f"raw/{raw_filename}", content_hash, mtime, "pending"),
                )
                sidecar[short_hash] = {
                    "original_path": rel_posix,
                    "raw_path": f"raw/{raw_filename}",
                    "content_hash": content_hash,
                    "mtime": mtime,
                    "loader": loader.name,
                }
                registered += 1
            except Exception as exc:  # noqa: BLE001 — bulk-walk resilience
                print(f"warning: failed to register {source_path}: {exc}", file=sys.stderr)
                continue
        conn.commit()

    if sidecar:
        (raw_dir / SOURCES_SIDECAR_NAME).write_text(json.dumps(sidecar, indent=2, sort_keys=True))

    return registered, skipped, dedup_skipped, empty_load_skipped


# Folders we never recurse into when discovering source markdown.
# - ``.mdwiki/`` holds the local cache + schema (never sources).
# - ``wiki/`` and ``raw/`` get scaffolded BY init; on a re-init or a partially-
#   prepared folder, files there are not user sources.
_EXCLUDED_DIR_NAMES: frozenset[str] = frozenset({WIKI_DIR_NAME, "wiki", "raw"})


def _iter_loadable_files(
    target: Path, *, registry: tuple[Loader, ...]
) -> list[Path]:
    """Return every file under ``target`` that some registered loader claims.

    Parameters
    ----------
    registry : tuple[Loader, ...]
        Pre-built loader registry. ``can_handle`` is checked directly against
        this registry — avoiding the per-file ``get_loader_for`` call that
        re-parses ``.mdwiki/config.toml``.

    Excludes ``.mdwiki/``, ``wiki/``, and ``raw/`` at any depth — those are
    mdwiki-managed locations and registering files there as user sources
    would pollute the source table on re-init. Files no loader recognizes
    are silently dropped (this is the registry's intended filter behavior;
    use ``mdwiki source`` afterwards to verify what was picked up).
    """
    out: list[Path] = []
    for p in target.rglob("*"):
        if not p.is_file():
            continue
        if not _EXCLUDED_DIR_NAMES.isdisjoint(p.parts):
            continue
        if not any(loader.can_handle(p) for loader in registry):
            continue
        out.append(p)
    return out


def _resolve_loader(path: Path, *, registry: tuple[Loader, ...]) -> Loader:
    """Return the first loader from ``registry`` that claims ``path``.

    Raises
    ------
    UnsupportedFiletypeError
        No registered loader claims this path's extension.
    """
    for loader in registry:
        if loader.can_handle(path):
            return loader
    raise UnsupportedFiletypeError(f"No loader registered for: {path}")


def _load_gitignore(target: Path) -> pathspec.PathSpec | None:
    """Parse ``target/.gitignore`` if present; return ``None`` otherwise."""
    gitignore_path = target / ".gitignore"
    if not gitignore_path.is_file():
        return None
    return pathspec.PathSpec.from_lines("gitwildmatch", gitignore_path.read_text().splitlines())


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(stem: str, *, max_len: int = 50) -> str:
    """Lowercase ``stem`` and collapse runs of non-alphanumerics into single hyphens.

    Parameters
    ----------
    stem : str
        File stem to slugify.
    max_len : int, optional
        Maximum length of the returned slug (default 50).
    """
    slug = _SLUG_RE.sub("-", stem.lower()).strip("-")
    return slug[:max_len] or "untitled"

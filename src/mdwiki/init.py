"""Scaffold a new ``.mdwiki/`` wiki and register every markdown source as ``pending``.

The starting state after ``init_wiki`` is intentionally empty:
``raw/`` holds content-addressed copies, ``wiki/`` holds nothing yet, and ``state.db``
records every source as ``pending`` ingest. The user picks how to bootstrap from there.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import pathspec
import tomli_w

from mdwiki.discover import WIKI_DIR_NAME, WikiNotFound, find_wiki
from mdwiki.state import connect, init_db

SOURCES_SIDECAR_NAME: str = ".sources.json"

DEFAULT_CONFIG: dict[str, dict[str, str | int | float | list[str]]] = {
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

## When to update vs. create — STRONG PREFERENCE FOR UPDATE

**Default to UPDATING an existing candidate page over creating a new one.** A wiki that grows by accretion of new pages is just a folder of notes. The compounding value comes from refining and extending existing pages.

Decision rule:
- **Update** if any candidate page covers ≥40% of the source's topic OR if your prospective new page would link primarily to one existing page
- **Create new page** ONLY when the source introduces a topic with no existing home, AND the topic warrants more than a paragraph anywhere
- **A single source ingest should typically touch 5–15 wiki pages** (mostly updates, plus 1–3 new pages and 3–8 cross-refs). If you propose only 1–2 new pages and zero updates, ask yourself whether you missed a candidate.

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
        Number of markdown sources copied to ``raw/`` and inserted into ``sources``.
    files_skipped : int
        Number of markdown files that were skipped (e.g. matched a ``.gitignore``).
    dedup_skipped : int
        Number of markdown files whose content matched an already-registered
        source (same SHA-256). The first occurrence wins; subsequent copies
        are skipped without overwriting the existing ``raw/`` file.
    wiki_root : Path
        The directory containing the new (or pre-existing) ``.mdwiki/``.
    message : str
        A short human-readable summary; CLI prints this verbatim.
    """

    created: bool
    files_registered: int
    files_skipped: int
    dedup_skipped: int
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

    registered, skipped, dedup_skipped = _register_sources(
        target=target, raw_dir=raw_dir, db_path=wiki_dir / "state.db"
    )

    suffix_parts: list[str] = []
    if skipped:
        suffix_parts.append(f"skipped {skipped} via .gitignore")
    if dedup_skipped:
        suffix_parts.append(f"skipped {dedup_skipped} duplicate-content")
    suffix = f" ({'; '.join(suffix_parts)})." if suffix_parts else "."

    return InitResult(
        created=True,
        files_registered=registered,
        files_skipped=skipped,
        dedup_skipped=dedup_skipped,
        wiki_root=target,
        message=(
            f"Initialized wiki at {target}/{WIKI_DIR_NAME}/. "
            f"Registered {registered} markdown source(s) as pending" + suffix
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


def _register_sources(*, target: Path, raw_dir: Path, db_path: Path) -> tuple[int, int, int]:
    """Walk ``target``, register every ``.md`` file as a pending source.

    Returns
    -------
    tuple[int, int, int]
        ``(registered, gitignore_skipped, dedup_skipped)``.

    Duplicate-content files (same SHA-256 prefix → same primary key) are
    skipped via ``INSERT OR IGNORE``; the first occurrence wins, the existing
    ``raw/<hash>-<slug>.md`` is left untouched, and a counter is bumped. Any
    other per-row failure is logged to stderr and skipped, so one bad file
    can't abort the whole walk.
    """
    import sys

    spec = _load_gitignore(target)
    md_paths = sorted(_iter_markdown_files(target))
    registered = 0
    skipped = 0
    dedup_skipped = 0
    sidecar: dict[str, dict[str, str | float]] = {}

    with connect(db_path) as conn:
        for md_path in md_paths:
            try:
                rel_posix = md_path.relative_to(target).as_posix()
                if spec is not None and spec.match_file(rel_posix):
                    skipped += 1
                    continue

                content_bytes = md_path.read_bytes()
                content_hash = hashlib.sha256(content_bytes).hexdigest()
                short_hash = content_hash[:12]

                # Pre-check by primary key: if another file with identical
                # content has already been registered this run, skip BOTH the
                # INSERT and the copyfile. Without this pre-check, two files
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

                slug = _slugify(md_path.stem)
                raw_filename = f"{short_hash}-{slug}.md"
                raw_path_abs = raw_dir / raw_filename
                # Don't clobber an existing raw/ file when the content is the
                # same — the first registration wins both in the DB and on disk.
                if not raw_path_abs.exists():
                    shutil.copyfile(md_path, raw_path_abs)
                mtime = md_path.stat().st_mtime

                conn.execute(
                    "INSERT INTO sources (id, original_path, raw_path, content_hash, mtime, status) VALUES (?, ?, ?, ?, ?, ?)",
                    (short_hash, rel_posix, f"raw/{raw_filename}", content_hash, mtime, "pending"),
                )
                sidecar[short_hash] = {
                    "original_path": rel_posix,
                    "raw_path": f"raw/{raw_filename}",
                    "content_hash": content_hash,
                    "mtime": mtime,
                }
                registered += 1
            except Exception as exc:  # noqa: BLE001 — bulk-walk resilience
                print(f"warning: failed to register {md_path}: {exc}", file=sys.stderr)
                continue
        conn.commit()

    if sidecar:
        (raw_dir / SOURCES_SIDECAR_NAME).write_text(json.dumps(sidecar, indent=2, sort_keys=True))

    return registered, skipped, dedup_skipped


# Folders we never recurse into when discovering source markdown.
# - ``.mdwiki/`` holds the local cache + schema (never sources).
# - ``wiki/`` and ``raw/`` get scaffolded BY init; on a re-init or a partially-
#   prepared folder, files there are not user sources.
_EXCLUDED_DIR_NAMES: frozenset[str] = frozenset({WIKI_DIR_NAME, "wiki", "raw"})


def _iter_markdown_files(target: Path) -> list[Path]:
    """Return every ``.md`` file under ``target``, excluding internal/scaffold dirs.

    Excludes ``.mdwiki/``, ``wiki/``, and ``raw/`` at any depth — those are
    mdwiki-managed locations and registering files there as user sources
    would pollute the source table on re-init.
    """
    return [p for p in target.rglob("*.md") if _EXCLUDED_DIR_NAMES.isdisjoint(p.parts)]


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

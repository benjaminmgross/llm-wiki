"""Scaffold a new ``.mdwiki/`` wiki and register every markdown source as ``pending``.

The starting state after ``init_wiki`` is intentionally empty:
``raw/`` holds content-addressed copies, ``wiki/`` holds nothing yet, and ``state.db``
records every source as ``pending`` ingest. The user picks how to bootstrap from there.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import pathspec
import tomli_w

from mdwiki.discover import WIKI_DIR_NAME, WikiNotFound, find_wiki
from mdwiki.state import connect, init_db

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

## When to update vs. create

- **Update existing page** when the new source extends, refines, or qualifies a claim already on a page
- **Create new page** when the source introduces a topic with no existing home, AND the topic warrants more than a single sentence anywhere

## Lint policies

- Pages with no inbound `cross_refs` are flagged as orphans
- Pages whose source files were modified after `last_touched_at` are flagged as stale
- Pages with header depth > 4 are flagged for restructuring
- Sources with backref count of 0 after ingest are flagged as poorly absorbed
"""

GITIGNORE_CONTENT: str = """\
# mdwiki local cache (rebuildable from raw/ + wiki/ + log.md)
state.db
state.db-journal
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
    wiki_root : Path
        The directory containing the new (or pre-existing) ``.mdwiki/``.
    message : str
        A short human-readable summary; CLI prints this verbatim.
    """

    created: bool
    files_registered: int
    files_skipped: int
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

    registered, skipped = _register_sources(target=target, raw_dir=raw_dir, db_path=wiki_dir / "state.db")

    return InitResult(
        created=True,
        files_registered=registered,
        files_skipped=skipped,
        wiki_root=target,
        message=(
            f"Initialized wiki at {target}/{WIKI_DIR_NAME}/. "
            f"Registered {registered} markdown source(s) as pending"
            + (f" (skipped {skipped} via .gitignore)." if skipped else ".")
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


def _register_sources(*, target: Path, raw_dir: Path, db_path: Path) -> tuple[int, int]:
    """Walk ``target``, register every ``.md`` file as a pending source, return (registered, skipped)."""
    spec = _load_gitignore(target)
    md_paths = sorted(_iter_markdown_files(target))
    registered = 0
    skipped = 0

    with connect(db_path) as conn:
        for md_path in md_paths:
            rel_posix = md_path.relative_to(target).as_posix()
            if spec is not None and spec.match_file(rel_posix):
                skipped += 1
                continue

            content_bytes = md_path.read_bytes()
            content_hash = hashlib.sha256(content_bytes).hexdigest()
            short_hash = content_hash[:12]
            slug = _slugify(md_path.stem)
            raw_filename = f"{short_hash}-{slug}.md"
            raw_path_abs = raw_dir / raw_filename
            shutil.copyfile(md_path, raw_path_abs)

            conn.execute(
                "INSERT INTO sources (id, original_path, raw_path, content_hash, mtime, status) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    short_hash,
                    rel_posix,
                    f"raw/{raw_filename}",
                    content_hash,
                    md_path.stat().st_mtime,
                    "pending",
                ),
            )
            registered += 1
        conn.commit()

    return registered, skipped


def _iter_markdown_files(target: Path) -> list[Path]:
    """Return every ``.md`` file under ``target``, excluding any inside ``.mdwiki/``."""
    return [p for p in target.rglob("*.md") if WIKI_DIR_NAME not in p.parts]


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

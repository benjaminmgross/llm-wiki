"""Runtime ``mdwiki skill`` — print the wiki's schema plus the packaged agent skill.

The installable skill (``SKILL.md`` intent router + one reference file per
operation) lives in this package under ``mdwiki/skill/`` so the runtime
command and the ``skills``-CLI-installable directory are the same bytes. The
repository-root ``skill/`` is a symlink to that directory.
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

from mdwiki.discover import WIKI_DIR_NAME

SKILL_ROOT: Path = Path(str(resources.files("mdwiki").joinpath("skill")))
REFERENCES_DIR: Path = SKILL_ROOT / "references"

_FRONTMATTER_RE = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.DOTALL)


class WikiNotFoundForSkill(FileNotFoundError):
    """Raised by ``run_skill`` when invoked outside a wiki folder."""


class UnknownWorkflowError(ValueError):
    """Raised when ``--workflow`` names a reference file that does not exist."""


def list_workflows() -> list[str]:
    """Return every packaged reference workflow name, sorted."""
    if not REFERENCES_DIR.is_dir():
        return []
    return sorted(path.stem for path in REFERENCES_DIR.glob("*.md"))


def router_text() -> str:
    """Return ``SKILL.md`` with its YAML frontmatter stripped."""
    text = (SKILL_ROOT / "SKILL.md").read_text()
    return _FRONTMATTER_RE.sub("", text, count=1).strip()


def workflow_text(name: str) -> str:
    """Return one reference workflow body.

    Raises
    ------
    UnknownWorkflowError
        When no ``references/<name>.md`` is packaged.
    """
    path = REFERENCES_DIR / f"{name}.md"
    if name not in list_workflows() or not path.is_file():
        available = ", ".join(list_workflows())
        raise UnknownWorkflowError(f"unknown workflow {name!r}; available: {available}")
    return path.read_text().strip()


def run_skill(wiki_root: Path, *, workflow: str | None = None) -> str:
    """Return the wiki's schema followed by the skill router and reference workflows.

    Parameters
    ----------
    wiki_root : Path
        Directory containing ``.mdwiki/schema.md``.
    workflow : str, optional
        When given, return only that reference workflow (no schema, no router).

    Raises
    ------
    WikiNotFoundForSkill
        When ``.mdwiki/schema.md`` doesn't exist under ``wiki_root``.
    UnknownWorkflowError
        When ``workflow`` is not a packaged reference.
    """
    if workflow is not None:
        return workflow_text(workflow)
    schema_path = wiki_root / WIKI_DIR_NAME / "schema.md"
    if not schema_path.is_file():
        raise WikiNotFoundForSkill(f"No schema at {schema_path}; not a wiki folder. Run `mdwiki init` first.")
    parts = [schema_path.read_text().rstrip(), router_text()]
    for name in list_workflows():
        parts.append(workflow_text(name))
    return "\n\n---\n\n".join(parts) + "\n"

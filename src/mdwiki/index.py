"""Generate ``wiki/index.md`` — the content-oriented catalog Karpathy specifies.

Called from inside ``IngestTransaction`` after every successful apply so the
index stays in sync with the wiki. The format is humans-first (markdown links
grouped by page kind with one-line summaries) and intentionally readable; on
query the LLM scans it as the entry point to the wiki.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from mdwiki.page_kinds import page_kind_folders_for_wiki
from mdwiki.version_chain import strip_frontmatter


def regenerate_index(wiki_root: Path) -> None:
    """Write a fresh ``wiki/index.md`` reflecting the current contents of ``wiki/``."""
    (wiki_root / "wiki").mkdir(exist_ok=True)
    (wiki_root / "wiki" / "index.md").write_text(build_index(wiki_root))


def build_index(wiki_root: Path) -> str:
    """Return the markdown body of ``wiki/index.md`` for the current wiki state."""
    today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    lines: list[str] = [
        "# Wiki Index",
        "",
        f"_Last updated: {today}_",
        "",
        "The LLM-maintained catalog of every page in this wiki, organized by kind.",
        "",
    ]

    for kind, heading, folder in page_kind_folders_for_wiki(wiki_root):
        entries = _collect_pages(wiki_root, folder)
        if kind == "source" and not entries:
            continue  # generated provenance section only appears once a source has been ingested
        lines.append(f"## {heading}")
        lines.append("")
        if entries:
            lines.extend(entries)
        else:
            lines.append("(none yet)")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _collect_pages(wiki_root: Path, folder: str) -> list[str]:
    """Build the bulleted entries for one page-kind folder."""
    folder_path = wiki_root / "wiki" / folder
    if not folder_path.is_dir():
        return []
    entries: list[str] = []
    for page_path in sorted(folder_path.glob("*.md")):
        if page_path.name in {"index.md", "log.md"}:
            continue
        title = _extract_title(page_path)
        summary = summarize_first_paragraph(strip_frontmatter(page_path.read_text()))
        rel_link = f"{folder}/{page_path.name}"
        if summary:
            entries.append(f"- [{title}]({rel_link}) — {summary}")
        else:
            entries.append(f"- [{title}]({rel_link})")
    return entries


def _extract_title(page_path: Path) -> str:
    """Return the first ``# H1`` heading, or fall back to the filename stem."""
    for line in strip_frontmatter(page_path.read_text()).splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return page_path.stem.replace("-", " ").title()


_PARAGRAPH_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_PARAGRAPH_EMPHASIS_RE = re.compile(r"(\*{1,3}|_{1,3})(\S(?:.*?\S)?)\1")
_PARAGRAPH_INLINE_CODE_RE = re.compile(r"`([^`]+)`")


def summarize_first_paragraph(page_text: str, *, max_chars: int = 200) -> str:
    """Return the first body paragraph stripped of markdown formatting, truncated."""
    page_text = strip_frontmatter(page_text)
    if not page_text.strip():
        return ""
    blocks = [b.strip() for b in page_text.split("\n\n") if b.strip()]
    for block in blocks:
        if block.startswith("#"):
            continue
        cleaned = _PARAGRAPH_LINK_RE.sub(r"\1", block)
        cleaned = _PARAGRAPH_INLINE_CODE_RE.sub(r"\1", cleaned)
        cleaned = _PARAGRAPH_EMPHASIS_RE.sub(r"\2", cleaned)
        cleaned = " ".join(cleaned.split())
        if len(cleaned) > max_chars:
            return cleaned[:max_chars] + "..."
        return cleaned
    return ""

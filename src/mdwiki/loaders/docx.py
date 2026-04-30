"""DOCX loader — extracts text via ``python-docx``, preserving heading levels and tables.

Heading paragraphs (``Heading 1``..``Heading 6``) become markdown headings (``#``..``######``).
Plain paragraphs round-trip as text. Tables become markdown tables. The conversion
is intentionally lossy — DOCX has many features (footnotes, comments, embedded objects)
that don't have clean markdown equivalents; we surface what the LLM can usefully read.
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from mdwiki.loaders.base import Loader


def _heading_level_from_style(style_name: str) -> int | None:
    """Return 1..6 if the style is ``Heading N``, otherwise ``None``."""
    if not style_name or not style_name.startswith("Heading "):
        return None
    try:
        level = int(style_name.removeprefix("Heading "))
    except ValueError:
        return None
    return level if 1 <= level <= 6 else None


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _format_table(table: Table) -> str:
    rows = [[cell.text for cell in row.cells] for row in table.rows]
    if not rows:
        return ""
    n_cols = max(len(r) for r in rows)
    rows = [r + [""] * (n_cols - len(r)) for r in rows]

    lines: list[str] = []
    header, body_rows = rows[0], rows[1:]
    lines.append("| " + " | ".join(_escape_cell(c) for c in header) + " |")
    lines.append("| " + " | ".join(["---"] * n_cols) + " |")
    for r in body_rows:
        lines.append("| " + " | ".join(_escape_cell(c) for c in r) + " |")
    return "\n".join(lines)


def _paragraph_to_markdown(p: Paragraph) -> str:
    text = p.text.strip()
    if not text:
        return ""
    level = _heading_level_from_style(p.style.name if p.style else "")
    if level is not None:
        return f"{'#' * level} {text}"
    return text


class DocxLoader(Loader):
    """Convert ``.docx`` to markdown preserving headings, paragraphs, and tables."""

    name: str = "DocxLoader"

    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".docx"

    def load_to_markdown(self, path: Path) -> str:
        doc = Document(str(path))
        blocks: list[str] = []

        # Walk in document order: docx body iterchildren yields paragraphs and tables interleaved
        for child in doc.element.body.iterchildren():
            tag = child.tag.split("}", 1)[-1]  # strip the namespace prefix
            if tag == "p":
                para = Paragraph(child, doc)
                md = _paragraph_to_markdown(para)
                if md:
                    blocks.append(md)
            elif tag == "tbl":
                table = Table(child, doc)
                md = _format_table(table)
                if md:
                    blocks.append(md)
            # Other elements (sectPr etc.) are skipped silently.

        return "\n\n".join(blocks) + ("\n" if blocks else "")

"""CSV / TSV loader — converts tabular data to a markdown table.

Truncates to the first 100 data rows with a "(N more rows truncated)" footer; large
CSVs would otherwise blow chunker context with row noise that the LLM can't usefully
synthesize. The truncation note is markdown so it survives downstream processing.
"""

from __future__ import annotations

import csv
from pathlib import Path

from mdwiki.loaders.base import Loader

_MAX_ROWS: int = 100
_CSV_SUFFIXES: frozenset[str] = frozenset({".csv", ".tsv"})


def _escape_cell(value: str) -> str:
    """Replace pipes and newlines so the cell can't break the markdown table grid."""
    return value.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


class CsvLoader(Loader):
    """Read CSV/TSV and emit a markdown table (header + separator + up to 100 rows)."""

    name: str = "CsvLoader"

    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() in _CSV_SUFFIXES

    def load_to_markdown(self, path: Path) -> str:
        delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
        with path.open(newline="") as f:
            reader = csv.reader(f, delimiter=delimiter)
            try:
                header = next(reader)
            except StopIteration:
                return ""

            rows: list[list[str]] = []
            truncated_count = 0
            for row in reader:
                if len(rows) >= _MAX_ROWS:
                    truncated_count = 1 + sum(1 for _ in reader)
                    break
                rows.append(row)

        return _format_table(header, rows, truncated_count)


def _format_table(header: list[str], rows: list[list[str]], truncated_count: int) -> str:
    n_cols = len(header)
    lines = [
        "| " + " | ".join(_escape_cell(c) for c in header) + " |",
        "| " + " | ".join(["---"] * n_cols) + " |",
    ]
    for row in rows:
        # Pad short rows / truncate long rows to header width so the grid stays valid
        cells = list(row) + [""] * (n_cols - len(row))
        cells = cells[:n_cols]
        lines.append("| " + " | ".join(_escape_cell(c) for c in cells) + " |")

    body = "\n".join(lines) + "\n"
    if truncated_count > 0:
        body += f"\n_({truncated_count} more rows truncated)_\n"
    return body

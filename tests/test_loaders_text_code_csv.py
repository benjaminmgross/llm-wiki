"""Tests for the stdlib loaders: ``TextLoader``, ``CodeLoader``, ``CsvLoader``."""

from __future__ import annotations

from pathlib import Path

import pytest

from mdwiki.loaders import get_loader_for
from mdwiki.loaders.code import CodeLoader
from mdwiki.loaders.csv_loader import CsvLoader
from mdwiki.loaders.text import TextLoader

# ----- TextLoader -----


@pytest.mark.unit
def test_text_loader_wraps_in_code_fence(tmp_path: Path) -> None:
    """Plain text wraps in a fenced block — preserves whitespace and line structure."""
    src = tmp_path / "notes.txt"
    src.write_text("line one\nline two\n")
    md = TextLoader().load_to_markdown(src)
    assert md.startswith("```")
    assert md.rstrip().endswith("```")
    assert "line one" in md
    assert "line two" in md


@pytest.mark.unit
def test_text_loader_can_handle_log_and_rst(tmp_path: Path) -> None:
    """``.log`` and ``.rst`` route to ``TextLoader`` too."""
    for ext in (".txt", ".log", ".rst"):
        src = tmp_path / f"x{ext}"
        src.write_text("body")
        loader = get_loader_for(src)
        assert isinstance(loader, TextLoader), f"{ext} did not route to TextLoader"


@pytest.mark.unit
def test_text_loader_does_not_claim_md_or_code(tmp_path: Path) -> None:
    """``TextLoader.can_handle`` returns False for filetypes other loaders own."""
    loader = TextLoader()
    assert loader.can_handle(Path("a.md")) is False
    assert loader.can_handle(Path("a.py")) is False
    assert loader.can_handle(Path("a.csv")) is False


# ----- CodeLoader -----


@pytest.mark.unit
def test_code_loader_uses_language_tag_from_extension(tmp_path: Path) -> None:
    """Python source produces a ```python fenced block."""
    src = tmp_path / "script.py"
    src.write_text("def foo(): pass\n")
    md = CodeLoader().load_to_markdown(src)
    assert md.startswith("```python")
    assert "def foo()" in md


@pytest.mark.unit
@pytest.mark.parametrize(
    ("ext", "expected_tag"),
    [
        (".py", "python"),
        (".js", "javascript"),
        (".ts", "typescript"),
        (".go", "go"),
        (".rs", "rust"),
        (".java", "java"),
        (".rb", "ruby"),
        (".sh", "bash"),
        (".sql", "sql"),
    ],
)
def test_code_loader_maps_extension_to_language_tag(tmp_path: Path, ext: str, expected_tag: str) -> None:
    """Each registered extension maps to its conventional fenced-block language tag."""
    src = tmp_path / f"x{ext}"
    src.write_text("# placeholder")
    md = CodeLoader().load_to_markdown(src)
    assert md.startswith(f"```{expected_tag}")


@pytest.mark.unit
def test_code_loader_unknown_extension_falls_back_to_no_tag(tmp_path: Path) -> None:
    """``can_handle`` is False for unregistered extensions — they get no fenced block."""
    loader = CodeLoader()
    assert loader.can_handle(Path("x.xyz123")) is False


# ----- CsvLoader -----


@pytest.mark.unit
def test_csv_loader_produces_markdown_table(tmp_path: Path) -> None:
    """CSV with header + 2 rows produces a 4-line markdown table (header + sep + 2 rows)."""
    src = tmp_path / "data.csv"
    src.write_text("name,age\nAlice,30\nBob,25\n")
    md = CsvLoader().load_to_markdown(src)
    assert "| name | age |" in md
    assert "| Alice | 30 |" in md
    assert "| Bob | 25 |" in md
    # Separator row between header and data
    assert "| --- | --- |" in md or "|---|---|" in md


@pytest.mark.unit
def test_csv_loader_handles_tsv(tmp_path: Path) -> None:
    """``.tsv`` routes to CsvLoader and uses tab as separator."""
    src = tmp_path / "data.tsv"
    src.write_text("name\tage\nAlice\t30\n")
    loader = get_loader_for(src)
    assert isinstance(loader, CsvLoader)
    md = loader.load_to_markdown(src)
    assert "| name | age |" in md
    assert "| Alice | 30 |" in md


@pytest.mark.unit
def test_csv_loader_truncates_large_files(tmp_path: Path) -> None:
    """Files with more than 100 data rows produce a truncation note + first 100 only."""
    src = tmp_path / "big.csv"
    rows = ["id,value"]
    rows.extend(f"{i},val-{i}" for i in range(150))
    src.write_text("\n".join(rows) + "\n")

    md = CsvLoader().load_to_markdown(src)

    assert "| 0 | val-0 |" in md
    assert "| 99 | val-99 |" in md
    assert "| 100 | val-100 |" not in md  # truncated
    assert "rows truncated" in md.lower()
    assert "50" in md  # 150 - 100 = 50 truncated


@pytest.mark.unit
def test_csv_loader_handles_empty_file(tmp_path: Path) -> None:
    """Empty CSV returns empty string (caller decides what to do with empty load)."""
    src = tmp_path / "empty.csv"
    src.write_text("")
    md = CsvLoader().load_to_markdown(src)
    assert md == ""


@pytest.mark.unit
def test_csv_loader_escapes_pipe_in_cell(tmp_path: Path) -> None:
    """A literal ``|`` inside a CSV cell must be escaped so the markdown table stays well-formed."""
    src = tmp_path / "pipes.csv"
    src.write_text("col1,col2\nhas|pipe,plain\n")
    md = CsvLoader().load_to_markdown(src)
    rows_with_pipes = [line for line in md.splitlines() if line.startswith("|") and "plain" in line]
    assert len(rows_with_pipes) == 1
    # The cell's pipe must be escaped — count of unescaped table-grid pipes is exactly 3
    unescaped_pipe_count = rows_with_pipes[0].replace("\\|", "").count("|")
    assert unescaped_pipe_count == 3, f"Cell pipe not escaped: {rows_with_pipes[0]!r}"

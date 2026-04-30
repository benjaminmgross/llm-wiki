"""End-to-end loader integration: ``init_wiki`` against a folder of mixed filetypes.

Catches regressions where the unit tests of individual loaders pass in isolation but
``init_wiki`` mis-routes a path, fails to record the loader name in the sidecar, or
writes loader output to the wrong raw/ filename. Phase 5 will extend this with
PDF / DOCX / HTML / image fixtures; Phase 2 covers stdlib-only filetypes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mdwiki.init import SOURCES_SIDECAR_NAME, init_wiki
from mdwiki.state import connect


@pytest.fixture
def mixed_filetype_corpus(tmp_path: Path) -> Path:
    """A folder containing one file of each Phase-2-supported filetype."""
    (tmp_path / "doc.md").write_text("# Markdown doc\n\nbody")
    (tmp_path / "notes.txt").write_text("plain text notes\n")
    (tmp_path / "script.py").write_text("def foo():\n    return 42\n")
    (tmp_path / "data.csv").write_text("name,age\nAlice,30\nBob,25\n")
    return tmp_path


@pytest.mark.integration
def test_init_registers_each_filetype_with_its_loader(mixed_filetype_corpus: Path) -> None:
    """All four filetypes register; sidecar names the correct loader per source."""
    result = init_wiki(mixed_filetype_corpus)

    assert result.files_registered == 4

    sidecar_path = mixed_filetype_corpus / "raw" / SOURCES_SIDECAR_NAME
    sidecar = json.loads(sidecar_path.read_text())

    loaders_by_path = {entry["original_path"]: entry["loader"] for entry in sidecar.values()}
    assert loaders_by_path == {
        "doc.md": "MarkdownLoader",
        "notes.txt": "TextLoader",
        "script.py": "CodeLoader",
        "data.csv": "CsvLoader",
    }


@pytest.mark.integration
def test_init_writes_loader_output_to_raw(mixed_filetype_corpus: Path) -> None:
    """``raw/<hash>-<slug>.md`` contains the loader's converted markdown, not the original bytes."""
    init_wiki(mixed_filetype_corpus)

    raw_dir = mixed_filetype_corpus / "raw"

    # Markdown passthrough → unchanged content
    md_files = list(raw_dir.glob("*-doc.md"))
    assert len(md_files) == 1
    assert md_files[0].read_text() == "# Markdown doc\n\nbody"

    # Text loader → fenced block
    txt_files = list(raw_dir.glob("*-notes.md"))
    assert len(txt_files) == 1
    assert txt_files[0].read_text().startswith("```")

    # Code loader → ```python fenced block
    py_files = list(raw_dir.glob("*-script.md"))
    assert len(py_files) == 1
    assert py_files[0].read_text().startswith("```python")

    # CSV loader → markdown table
    csv_files = list(raw_dir.glob("*-data.md"))
    assert len(csv_files) == 1
    csv_md = csv_files[0].read_text()
    assert "| name | age |" in csv_md
    assert "| Alice | 30 |" in csv_md


@pytest.mark.integration
def test_init_skips_unsupported_filetype(tmp_path: Path) -> None:
    """A file matching no registered loader is silently skipped, not registered."""
    (tmp_path / "supported.md").write_text("# md")
    (tmp_path / "skip.weirdext").write_text("nothing claims this")

    result = init_wiki(tmp_path)

    assert result.files_registered == 1
    with connect(tmp_path / ".mdwiki" / "state.db") as conn:
        rows = conn.execute("SELECT original_path FROM sources").fetchall()
    assert {r["original_path"] for r in rows} == {"supported.md"}

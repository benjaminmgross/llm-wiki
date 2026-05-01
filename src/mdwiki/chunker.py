"""
Chunk markdown documents by H2 headers with metadata extraction.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import TypedDict


class Section(TypedDict):
    """A single section extracted from a markdown file.

    Sections originate from one of four chunking strategies (recorded in
    ``chunking_strategy``): ``"h2"`` (preferred), ``"h1"`` (fallback when no
    H2 exists), ``"paragraph"`` (split on ``\\n\\n`` when no headers exist),
    or ``"sliding_window"`` (fixed-token windows for header-less, paragraph-
    less content like one-line giant blobs).
    """

    section_id: str
    source_file: str
    heading: str
    content: str
    last_modified: str
    word_count: int
    fingerprint: str
    chunking_strategy: str


class MarkdownChunker:
    """Chunk markdown files by H2 headers."""

    def __init__(self, min_section_words: int = 10):
        """
        Initialize chunker.

        Parameters
        ----------
        min_section_words : int, default=10
            Minimum words for a section to be included.
        """
        self.min_section_words = min_section_words

    def chunk_file(self, filepath: Path) -> list[Section]:
        """
        Chunk a single markdown file. Tries H2 first; falls back through H1,
        paragraph splits, and finally fixed-token sliding windows so that
        unstructured sources (transcripts, prose, single-line blobs) still
        produce usable sections rather than zero.

        Parameters
        ----------
        filepath : Path
            Path to markdown file.

        Returns
        -------
        list[Section]
            One entry per chunked section, ordered by source position.
        """
        content = filepath.read_text(encoding='utf-8')

        # Strip frontmatter
        if content.startswith('---'):
            parts = content.split('---', 2)
            if len(parts) >= 3:
                content = parts[2].strip()

        # Get file modification time
        try:
            mtime = datetime.fromtimestamp(filepath.stat().st_mtime).isoformat()
        except Exception:
            mtime = datetime.now().isoformat()

        # Tiered chunking: prefer H2, then H1, then paragraphs, then a fixed
        # window. Each tier returns [] when it produces zero qualifying
        # sections; only then do we descend to the next.
        sections = self._chunk_by_heading(
            content=content, filepath=filepath, mtime=mtime, level=2, strategy="h2",
        )
        if sections:
            return sections

        sections = self._chunk_by_heading(
            content=content, filepath=filepath, mtime=mtime, level=1, strategy="h1",
        )
        if sections:
            return sections

        sections = self._chunk_by_paragraphs(
            content=content, filepath=filepath, mtime=mtime,
        )
        if sections:
            return sections

        return self._chunk_by_sliding_window(
            content=content, filepath=filepath, mtime=mtime,
        )

    def _chunk_by_heading(
        self,
        *,
        content: str,
        filepath: Path,
        mtime: str,
        level: int,
        strategy: str,
    ) -> list[Section]:
        """Split ``content`` on the given heading level (1 or 2). Returns [] if no headings match."""
        # ``^# `` for H1, ``^## `` for H2, etc. Anchored to start-of-line.
        pattern = re.compile(r'^' + ('#' * level) + r' (.+)$', re.MULTILINE)
        matches = list(pattern.finditer(content))
        if not matches:
            return []

        sections: list[Section] = []
        for i, match in enumerate(matches):
            heading = match.group(1).strip()
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            section_content = content[start:end].strip()
            word_count = len(section_content.split())
            if word_count < self.min_section_words:
                continue
            sections.append(self._make_section(
                section_id=f"{filepath.name}/{heading}",
                source_file=str(filepath),
                heading=heading,
                content=section_content,
                mtime=mtime,
                strategy=strategy,
            ))
        return sections

    def _chunk_by_paragraphs(
        self,
        *,
        content: str,
        filepath: Path,
        mtime: str,
    ) -> list[Section]:
        """Split ``content`` on paragraph boundaries (``\\n\\n``). Returns [] if only one paragraph."""
        # Strip a leading H1 if present (no other headings would have made it
        # past _chunk_by_heading); a stand-alone H1 followed by prose should
        # still chunk on paragraphs of the prose.
        text = re.sub(r'^# .+\n+', '', content, count=1, flags=re.MULTILINE)
        paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]
        if len(paragraphs) < 2:
            return []

        sections: list[Section] = []
        for i, para in enumerate(paragraphs):
            word_count = len(para.split())
            if word_count < self.min_section_words:
                continue
            heading = f"paragraph-{i + 1}"
            sections.append(self._make_section(
                section_id=f"{filepath.name}/{heading}",
                source_file=str(filepath),
                heading=heading,
                content=para,
                mtime=mtime,
                strategy="paragraph",
            ))
        return sections

    def _chunk_by_sliding_window(
        self,
        *,
        content: str,
        filepath: Path,
        mtime: str,
        window_words: int = 800,
        overlap_words: int = 100,
    ) -> list[Section]:
        """Last-resort fallback: split content into ~``window_words`` overlapping windows.

        Produces at least one section even for short inputs, so a caller
        always gets a non-empty list when ``content`` is non-empty.
        """
        words = content.split()
        if not words:
            return []

        sections: list[Section] = []
        step = max(window_words - overlap_words, 1)
        index = 1
        for start in range(0, len(words), step):
            window = words[start:start + window_words]
            if len(window) < self.min_section_words and sections:
                # Only skip undersized windows AFTER we've emitted at least
                # one — that keeps short corpora from producing zero output.
                break
            chunk_text = ' '.join(window)
            heading = f"window-{index}"
            sections.append(self._make_section(
                section_id=f"{filepath.name}/{heading}",
                source_file=str(filepath),
                heading=heading,
                content=chunk_text,
                mtime=mtime,
                strategy="sliding_window",
            ))
            index += 1
            if start + window_words >= len(words):
                break
        return sections

    @staticmethod
    def _make_section(
        *,
        section_id: str,
        source_file: str,
        heading: str,
        content: str,
        mtime: str,
        strategy: str,
    ) -> Section:
        """Bundle the section dict with a content fingerprint for dedupe."""
        normalized = re.sub(r'[^\w\s]', '', content.lower())
        normalized = ' '.join(normalized.split())
        fingerprint = hashlib.md5(normalized.encode()).hexdigest()
        return {
            'section_id': section_id,
            'source_file': source_file,
            'heading': heading,
            'content': content,
            'last_modified': mtime,
            'word_count': len(content.split()),
            'fingerprint': fingerprint,
            'chunking_strategy': strategy,
        }

    def chunk_directory(
        self,
        directory: Path,
        exclude_patterns: list[str] | None = None,
    ) -> list[Section]:
        """
        Chunk all markdown files in a directory.

        Parameters
        ----------
        directory : Path
            Directory to process.
        exclude_patterns : list[str] | None
            Glob patterns to exclude.

        Returns
        -------
        list[Section]
            All sections from all files.
        """
        exclude_patterns = exclude_patterns or []
        all_sections: list[Section] = []

        for md_file in directory.rglob('*.md'):
            # Check exclusions
            rel_path = str(md_file.relative_to(directory))
            excluded = False
            for pattern in exclude_patterns:
                if re.match(pattern.replace('*', '.*'), rel_path):
                    excluded = True
                    break

            if excluded:
                continue

            try:
                sections = self.chunk_file(md_file)
                all_sections.extend(sections)
            except Exception as e:
                print(f"Warning: Error processing {md_file}: {e}")

        return all_sections

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
    """A single H2 section extracted from a markdown file."""

    section_id: str
    source_file: str
    heading: str
    content: str
    last_modified: str
    word_count: int
    fingerprint: str


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
        Chunk a single markdown file by H2 headers.

        Parameters
        ----------
        filepath : Path
            Path to markdown file.

        Returns
        -------
        list[Section]
            List of sections, one per H2 heading.
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

        # Split by H2 headers
        sections: list[Section] = []
        h2_pattern = re.compile(r'^## (.+)$', re.MULTILINE)

        matches = list(h2_pattern.finditer(content))

        for i, match in enumerate(matches):
            heading = match.group(1).strip()
            start = match.end()

            # Find end (next H2 or end of content)
            if i + 1 < len(matches):
                end = matches[i + 1].start()
            else:
                end = len(content)

            section_content = content[start:end].strip()
            word_count = len(section_content.split())

            if word_count < self.min_section_words:
                continue

            # Compute fingerprint for deduplication
            normalized = re.sub(r'[^\w\s]', '', section_content.lower())
            normalized = ' '.join(normalized.split())
            fingerprint = hashlib.md5(normalized.encode()).hexdigest()

            sections.append({
                'section_id': f"{filepath.name}/{heading}",
                'source_file': str(filepath),
                'heading': heading,
                'content': section_content,
                'last_modified': mtime,
                'word_count': word_count,
                'fingerprint': fingerprint,
            })

        return sections

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

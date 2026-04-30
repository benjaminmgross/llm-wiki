"""
Inventory and analyze markdown files in a directory.
Extracts metadata, structure, content fingerprints, and relationships.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

import yaml


class FileInventory(TypedDict, total=False):
    """Type definition for file inventory entry."""
    path: str
    filename: str
    size_bytes: int
    modified_fs: str
    modified_frontmatter: str | None
    frontmatter: dict[str, Any]
    word_count: int
    line_count: int
    headers: list[dict[str, Any]]
    sections: list[dict[str, Any]]
    links: dict[str, list[Any]]
    fingerprint: str
    title: str
    error: str | None


def extract_frontmatter(*, content: str) -> tuple[dict[str, Any], str]:
    """
    Extract YAML frontmatter from markdown content.

    Parameters
    ----------
    content : str
        Raw markdown content with optional YAML frontmatter.

    Returns
    -------
    tuple[dict[str, Any], str]
        Tuple of (frontmatter dict, remaining content).
    """
    if content.startswith('---'):
        parts = content.split('---', 2)
        if len(parts) >= 3:
            try:
                frontmatter = yaml.safe_load(parts[1]) or {}
                body = parts[2].strip()
                return frontmatter, body
            except yaml.YAMLError:
                pass
    return {}, content


def extract_headers(*, content: str) -> list[dict[str, Any]]:
    """
    Extract all markdown headers with their levels and line numbers.

    Parameters
    ----------
    content : str
        Markdown content to parse for headers.

    Returns
    -------
    list[dict[str, Any]]
        List of header dicts with level, text, and line number.
    """
    headers = []
    for i, line in enumerate(content.split('\n'), 1):
        match = re.match(r'^(#{1,6})\s+(.+)$', line)
        if match:
            headers.append({
                'level': len(match.group(1)),
                'text': match.group(2).strip(),
                'line': i
            })
    return headers


def extract_links(*, content: str) -> dict[str, list[Any]]:
    """
    Extract internal and external links from markdown.

    Parameters
    ----------
    content : str
        Markdown content to parse for links.

    Returns
    -------
    dict[str, list[Any]]
        Dictionary with 'internal' and 'external' link lists.
    """
    # Wikilinks: [[target]] or [[target|display]]
    wikilinks = re.findall(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', content)

    # Standard markdown links: [text](url)
    md_links = re.findall(r'\[([^\]]+)\]\(([^)]+)\)', content)

    internal: list[str] = list(set(wikilinks))
    external: list[dict[str, str]] = []

    for text, url in md_links:
        if url.startswith(('http://', 'https://')):
            external.append({'text': text, 'url': url})
        elif not url.startswith('#'):
            # Internal markdown link
            internal.append(url.split('#')[0])

    return {
        'internal': list(set(internal)),
        'external': external
    }


def compute_fingerprint(*, content: str) -> str:
    """
    Compute content fingerprint for similarity detection.

    Parameters
    ----------
    content : str
        Content to fingerprint.

    Returns
    -------
    str
        MD5 hash of normalized content.
    """
    # Normalize: lowercase, remove extra whitespace, remove punctuation
    normalized = re.sub(r'[^\w\s]', '', content.lower())
    normalized = ' '.join(normalized.split())
    return hashlib.md5(normalized.encode()).hexdigest()


def extract_sections(*, content: str, headers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Extract content sections based on headers.

    Parameters
    ----------
    content : str
        Full markdown content.
    headers : list[dict[str, Any]]
        List of headers from extract_headers.

    Returns
    -------
    list[dict[str, Any]]
        List of section dicts with heading, level, line ranges, word count, fingerprint.
    """
    lines = content.split('\n')
    sections = []

    for i, header in enumerate(headers):
        start_line = header['line']
        if i + 1 < len(headers):
            end_line = headers[i + 1]['line'] - 1
        else:
            end_line = len(lines)

        section_content = '\n'.join(lines[start_line:end_line])
        word_count = len(section_content.split())

        sections.append({
            'heading': header['text'],
            'level': header['level'],
            'start_line': start_line,
            'end_line': end_line,
            'word_count': word_count,
            'fingerprint': compute_fingerprint(content=section_content),
        })

    return sections


def analyze_file(*, filepath: Path) -> FileInventory:
    """
    Analyze a single markdown file.

    Parameters
    ----------
    filepath : Path
        Path to the markdown file.

    Returns
    -------
    FileInventory
        Dictionary with file metadata, structure, and content analysis.
    """
    try:
        content = filepath.read_text(encoding='utf-8')
    except Exception as e:
        return {'error': str(e), 'path': str(filepath)}

    stat = filepath.stat()
    frontmatter, body = extract_frontmatter(content=content)
    headers = extract_headers(content=body)
    links = extract_links(content=body)
    sections = extract_sections(content=body, headers=headers)

    # Get modification date from frontmatter if available
    modified_frontmatter = None
    for key in ['modified', 'updated', 'last_modified', 'date']:
        if key in frontmatter:
            modified_frontmatter = str(frontmatter[key])
            break

    return {
        'path': str(filepath),
        'filename': filepath.name,
        'size_bytes': stat.st_size,
        'modified_fs': datetime.fromtimestamp(stat.st_mtime).isoformat(),
        'modified_frontmatter': modified_frontmatter,
        'frontmatter': frontmatter,
        'word_count': len(body.split()),
        'line_count': len(content.split('\n')),
        'headers': headers,
        'sections': sections,
        'links': links,
        'fingerprint': compute_fingerprint(content=body),
        'title': frontmatter.get('title') or (headers[0]['text'] if headers else filepath.stem)
    }


def inventory_directory(
    *,
    directory: Path,
    exclude_patterns: list[str] | None = None,
) -> list[FileInventory]:
    """
    Inventory all markdown files in a directory.

    Parameters
    ----------
    directory : Path
        Directory to analyze.
    exclude_patterns : list[str] | None, default=None
        Glob patterns to exclude.

    Returns
    -------
    list[FileInventory]
        List of file inventory entries, sorted by modification time (newest first).
    """
    exclude_patterns = exclude_patterns or []
    files: list[FileInventory] = []

    for filepath in directory.rglob('*.md'):
        # Check exclusion patterns
        rel_path = str(filepath.relative_to(directory))
        excluded = False
        for pattern in exclude_patterns:
            if re.match(pattern.replace('*', '.*'), rel_path):
                excluded = True
                break

        if not excluded:
            files.append(analyze_file(filepath=filepath))

    return sorted(files, key=lambda x: x.get('modified_fs', ''), reverse=True)

"""
Synthesize consolidated markdown files from clusters.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypedDict

import yaml

SynthesisStrategy = Literal['authority', 'comprehensive', 'canonical']


class SynthesisResult(TypedDict):
    """Result from synthesizing a cluster."""
    cluster_id: str
    output_file: str
    source_files: list[str]
    primary_file: str
    strategy: str


def synthesize_cluster(
    *,
    cluster: dict[str, Any],
    output_dir: Path,
    strategy: SynthesisStrategy = 'authority',
) -> SynthesisResult:
    """
    Synthesize a single cluster into one consolidated file.

    Parameters
    ----------
    cluster : dict[str, Any]
        Cluster definition from cluster_files containing files, primary_file, theme.
    output_dir : Path
        Directory to write consolidated file.
    strategy : SynthesisStrategy, default='authority'
        Merge strategy ('authority', 'comprehensive', 'canonical').

    Returns
    -------
    SynthesisResult
        Dictionary with output file path and source files.

    Examples
    --------
    >>> result = synthesize_cluster(
    ...     cluster={'files': ['a.md', 'b.md'], 'primary_file': 'a.md', 'theme': 'Topic'},
    ...     output_dir=Path('./out'),
    ... )
    """
    files = cluster['files']
    primary = cluster['primary_file']
    theme = cluster.get('theme', 'Consolidated')

    # Read all files
    contents: dict[str, str] = {}
    frontmatters: dict[str, dict[str, Any]] = {}

    for f in files:
        try:
            content = Path(f).read_text(encoding='utf-8')
            if content.startswith('---'):
                parts = content.split('---', 2)
                if len(parts) >= 3:
                    frontmatters[f] = yaml.safe_load(parts[1]) or {}
                    contents[f] = parts[2].strip()
                else:
                    contents[f] = content
            else:
                contents[f] = content
        except Exception as e:
            contents[f] = f"<!-- Error reading: {e} -->"

    # Get modification times
    mod_times: dict[str, datetime] = {}
    for f in files:
        try:
            mod_times[f] = datetime.fromtimestamp(Path(f).stat().st_mtime)
        except Exception:
            mod_times[f] = datetime.min

    # Sort by modification time (most recent first)
    sorted_files = sorted(
        files,
        key=lambda x: mod_times.get(x, datetime.min),
        reverse=True
    )

    # Build consolidated content
    consolidated_lines: list[str] = []

    # Create frontmatter
    consolidated_lines.append('---')
    consolidated_lines.append(f'title: {theme}')
    consolidated_lines.append('consolidated_from:')
    for f in sorted_files:
        consolidated_lines.append(f'  - file: {Path(f).name}')
        consolidated_lines.append(f'    modified: {mod_times[f].isoformat()}')
    consolidated_lines.append(f'consolidated_at: {datetime.now().isoformat()}')
    consolidated_lines.append(f'strategy: {strategy}')
    consolidated_lines.append('---')
    consolidated_lines.append('')

    # Add title
    consolidated_lines.append(f'# {theme}')
    consolidated_lines.append('')

    if strategy == 'authority':
        # Most recent file is authoritative
        primary_content = contents.get(primary, '')
        consolidated_lines.append(f'<!-- PRIMARY SOURCE: {Path(primary).name} -->')
        consolidated_lines.append(primary_content)
        consolidated_lines.append('')

        # Add unique sections from other files
        for f in sorted_files:
            if f == primary:
                continue
            content = contents.get(f, '')
            if content.strip():
                consolidated_lines.append(f'<!-- SUPPLEMENTARY: {Path(f).name} -->')
                consolidated_lines.append('')
                consolidated_lines.append(f'## From {Path(f).stem}')
                consolidated_lines.append('')
                consolidated_lines.append(content)
                consolidated_lines.append('')

    elif strategy == 'comprehensive':
        # Include all content, mark conflicts
        for f in sorted_files:
            content = contents.get(f, '')
            if content.strip():
                consolidated_lines.append(
                    f'<!-- SOURCE: {Path(f).name} '
                    f'(modified: {mod_times[f].isoformat()}) -->'
                )
                consolidated_lines.append('')
                consolidated_lines.append(f'## {Path(f).stem}')
                consolidated_lines.append('')
                consolidated_lines.append(content)
                consolidated_lines.append('')

    elif strategy == 'canonical':
        # Only use primary file
        primary_content = contents.get(primary, '')
        consolidated_lines.append(f'<!-- CANONICAL SOURCE: {Path(primary).name} -->')
        consolidated_lines.append(primary_content)

        # Add references to other files
        if len(files) > 1:
            consolidated_lines.append('')
            consolidated_lines.append('---')
            consolidated_lines.append('')
            consolidated_lines.append('## Related Documents')
            consolidated_lines.append('')
            for f in sorted_files:
                if f != primary:
                    consolidated_lines.append(f'- [{Path(f).stem}]({Path(f).name})')

    # Create output file
    safe_name = re.sub(r'[^\w\s-]', '', theme.lower()).replace(' ', '-')[:50]
    output_file = output_dir / f'{safe_name}.md'

    # Handle name conflicts
    counter = 1
    while output_file.exists():
        output_file = output_dir / f'{safe_name}-{counter}.md'
        counter += 1

    output_file.write_text('\n'.join(consolidated_lines))

    return {
        'cluster_id': cluster['id'],
        'output_file': str(output_file),
        'source_files': files,
        'primary_file': primary,
        'strategy': strategy
    }


def synthesize_all(
    *,
    clusters: list[dict[str, Any]],
    output_dir: Path,
    strategy: SynthesisStrategy = 'authority',
    min_files: int = 2,
) -> list[SynthesisResult]:
    """
    Synthesize all clusters that meet minimum file threshold.

    Parameters
    ----------
    clusters : list[dict[str, Any]]
        List of clusters from cluster_files.
    output_dir : Path
        Directory to write consolidated files.
    strategy : SynthesisStrategy, default='authority'
        Merge strategy.
    min_files : int, default=2
        Minimum files required to process a cluster.

    Returns
    -------
    list[SynthesisResult]
        List of synthesis results for each processed cluster.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[SynthesisResult] = []
    for cluster in clusters:
        if len(cluster['files']) >= min_files:
            result = synthesize_cluster(
                cluster=cluster,
                output_dir=output_dir,
                strategy=strategy,
            )
            results.append(result)

    return results


def synthesize_from_manifest(
    *,
    manifest: dict[str, Any],
    output_dir: Path,
    strategy: SynthesisStrategy = 'authority',
) -> list[SynthesisResult]:
    """
    Synthesize markdown files from a parsed manifest.

    Parameters
    ----------
    manifest : dict
        Parsed manifest from ManifestParser.
    output_dir : Path
        Directory to write output files.
    strategy : SynthesisStrategy
        Merge strategy for section content.

    Returns
    -------
    list[SynthesisResult]
        List of synthesis results.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    source_dir = Path(manifest.get('source', '.'))
    results: list[SynthesisResult] = []

    # Build section content lookup
    section_contents: dict[str, dict[str, Any]] = {}
    _load_section_contents(source_dir, section_contents)

    for theme in manifest.get('hierarchy', []):
        for doc in theme.get('documents', []):
            doc_name = doc['name']
            sections = doc.get('sections', [])

            # Skip duplicates unless explicitly kept
            active_sections = [s for s in sections if not s.get('duplicate_of')]

            if not active_sections:
                continue

            # Build document content
            lines: list[str] = []
            lines.append('---')
            lines.append(f"title: {theme['theme']}")
            lines.append(f"generated: {datetime.now().isoformat()}")
            lines.append('---')
            lines.append('')
            lines.append(f"# {theme['theme']}")
            lines.append('')

            for section in active_sections:
                section_id = section['id']
                content = section_contents.get(section_id, {}).get('content', '')
                heading = section.get('heading', 'Untitled')

                lines.append(f"## {heading}")
                lines.append('')
                lines.append(content)
                lines.append('')

            # Write file
            output_file = output_dir / doc_name
            output_file.write_text('\n'.join(lines))

            results.append({
                'cluster_id': theme['theme'],
                'output_file': str(output_file),
                'source_files': [s['id'] for s in active_sections],
                'primary_file': active_sections[0]['id'] if active_sections else '',
                'strategy': strategy,
            })

    # Handle orphans with 'standalone' action
    for orphan in manifest.get('orphans', []):
        if orphan.get('suggested_action') == 'standalone':
            section_id = orphan['id']
            content = section_contents.get(section_id, {}).get('content', '')
            heading = orphan.get('heading', 'Untitled')

            lines = [
                '---',
                f"title: {heading}",
                f"generated: {datetime.now().isoformat()}",
                '---',
                '',
                f"# {heading}",
                '',
                content,
            ]

            safe_name = re.sub(r'[^\w\s-]', '', heading.lower()).replace(' ', '-')[:50]
            output_file = output_dir / f"{safe_name}.md"
            output_file.write_text('\n'.join(lines))

    return results


def _load_section_contents(source_dir: Path, lookup: dict[str, dict[str, Any]]) -> None:
    """Load section contents from source files into lookup dict."""
    from .chunker import MarkdownChunker

    chunker = MarkdownChunker()
    for md_file in source_dir.rglob('*.md'):
        try:
            sections = chunker.chunk_file(md_file)
            for section in sections:
                lookup[section['section_id']] = section
        except Exception:
            pass

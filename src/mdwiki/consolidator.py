"""
Main consolidation pipeline.
Orchestrates inventory → analyze → cluster → synthesize → validate.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

from .clustering import ClusterMethod, cluster_files
from .inventory import inventory_directory
from .relationships import analyze_relationships
from .synthesis import SynthesisStrategy, synthesize_all


class ConsolidationResult(TypedDict):
    """Result from running the consolidation pipeline."""
    source_directory: str
    output_directory: str
    strategy: str
    method: str
    threshold: float
    files_analyzed: int
    clusters_created: int
    files_consolidated: int
    files_created: int
    coverage: float
    validation: dict[str, Any]
    synthesis_results: list[dict[str, Any]]
    non_markdown_copied: int


def _copy_non_markdown_files(
    *,
    source_dir: Path,
    output_dir: Path,
    exclude_patterns: list[str] | None = None,
) -> list[str]:
    """
    Copy all non-markdown files from source to output, preserving structure.

    Parameters
    ----------
    source_dir : Path
        Source directory containing files.
    output_dir : Path
        Output directory to copy files to.
    exclude_patterns : list[str] | None, default=None
        Glob patterns to exclude.

    Returns
    -------
    list[str]
        List of copied file paths (relative to output_dir).
    """
    exclude_patterns = exclude_patterns or []
    copied: list[str] = []

    # Resolve paths to handle output_dir inside source_dir
    resolved_output = output_dir.resolve()

    for filepath in source_dir.rglob('*'):
        # Skip directories and markdown files
        if filepath.is_dir() or filepath.suffix.lower() == '.md':
            continue

        # Skip files inside the output directory (prevents infinite recursion)
        try:
            filepath.resolve().relative_to(resolved_output)
            continue  # File is inside output_dir, skip it
        except ValueError:
            pass  # File is not inside output_dir, proceed

        # Check exclusions
        rel_path = filepath.relative_to(source_dir)
        excluded = False
        for pattern in exclude_patterns:
            if re.match(pattern.replace('*', '.*'), str(rel_path)):
                excluded = True
                break

        if excluded:
            continue

        # Create target path and copy
        target = output_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(filepath, target)
        copied.append(str(rel_path))

    return copied


def validate_consolidation(
    *,
    synthesis_results: list[dict[str, Any]],
    source_dir: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """
    Validate the consolidation results.

    Parameters
    ----------
    synthesis_results : list[dict[str, Any]]
        List of synthesis results from synthesize_all.
    source_dir : Path
        Original source directory containing markdown files.
    output_dir : Path
        Directory containing consolidated output files.

    Returns
    -------
    dict[str, Any]
        Validation results with coverage, broken links, and status.
    """
    import re

    source_files = list(source_dir.rglob('*.md'))
    output_files = [
        f for f in output_dir.glob('*.md')
        if not f.name.startswith('_')
    ]

    # Check coverage
    consolidated_sources: set[str] = set()
    for result in synthesis_results:
        consolidated_sources.update(result['source_files'])

    uncovered = [
        str(f) for f in source_files
        if str(f) not in consolidated_sources
    ]

    # Check for broken links
    broken_links: list[dict[str, str]] = []
    for output_file in output_files:
        content = output_file.read_text()
        links = re.findall(r'\[([^\]]+)\]\(([^)]+)\)', content)
        for text, target in links:
            if target.startswith(('http://', 'https://', '#')):
                continue
            target_path = (output_file.parent / target).resolve()
            if not target_path.exists():
                broken_links.append({
                    'file': str(output_file),
                    'link_text': text,
                    'target': target
                })

    coverage_pct = (
        len(consolidated_sources) / len(source_files) * 100
        if source_files else 0
    )

    return {
        'total_source_files': len(source_files),
        'files_consolidated': len(consolidated_sources),
        'files_created': len(output_files),
        'coverage_percentage': round(coverage_pct, 1),
        'uncovered_files': uncovered[:20],
        'broken_links': broken_links,
        'status': 'success' if not broken_links else 'warnings'
    }


def consolidate(
    *,
    source_dir: str | Path,
    output_dir: str | Path,
    strategy: SynthesisStrategy = 'authority',
    threshold: float = 0.5,
    method: ClusterMethod = 'topic',
    exclude_patterns: list[str] | None = None,
    keep_work_files: bool = False,
    preserve_subfolders: bool = False,
) -> ConsolidationResult:
    """
    Run the full consolidation pipeline.

    Parameters
    ----------
    source_dir : str | Path
        Directory containing markdown files to consolidate.
    output_dir : str | Path
        Directory to write consolidated files.
    strategy : SynthesisStrategy, default='authority'
        Merge strategy ('authority', 'comprehensive', 'canonical').
    threshold : float, default=0.5
        Similarity threshold for clustering (0-1).
    method : ClusterMethod, default='topic'
        Clustering method ('topic', 'temporal', 'hierarchical', 'links', 'all').
    exclude_patterns : list[str] | None, default=None
        Glob patterns to exclude from processing.
    keep_work_files : bool, default=False
        Whether to keep intermediate JSON files.
    preserve_subfolders : bool, default=False
        If True, copy non-markdown files from source to output preserving
        directory structure.

    Returns
    -------
    ConsolidationResult
        Dictionary with summary, validation, and synthesis results.

    Examples
    --------
    >>> result = consolidate(source_dir='./docs', output_dir='./consolidated')
    >>> print(f"Created {result['files_created']} files")
    >>> print(f"Coverage: {result['coverage']}%")
    """
    source_path = Path(source_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Step 1: Inventory
    files = inventory_directory(directory=source_path, exclude_patterns=exclude_patterns)
    inventory = {
        'source_directory': str(source_path.absolute()),
        'analyzed_at': datetime.now().isoformat(),
        'file_count': len(files),
        'total_words': sum(f.get('word_count', 0) for f in files if 'error' not in f),
        'files': files
    }

    if keep_work_files:
        (output_path / '_inventory.json').write_text(
            json.dumps(inventory, indent=2, default=str)
        )

    # Step 2: Analyze relationships
    relationships = analyze_relationships(inventory=inventory, threshold=threshold)

    if keep_work_files:
        (output_path / '_relationships.json').write_text(
            json.dumps(relationships, indent=2, default=str)
        )

    # Step 3: Cluster
    clusters = cluster_files(relationships=relationships, method=method, threshold=threshold)

    if keep_work_files:
        (output_path / '_clusters.json').write_text(
            json.dumps(clusters, indent=2, default=str)
        )

    # Step 4: Synthesize
    synthesis_results = synthesize_all(
        clusters=clusters,
        output_dir=output_path,
        strategy=strategy,
    )

    # Step 5: Copy non-markdown files if requested
    copied_files: list[str] = []
    if preserve_subfolders:
        copied_files = _copy_non_markdown_files(
            source_dir=source_path,
            output_dir=output_path,
            exclude_patterns=exclude_patterns,
        )

    # Step 6: Validate
    validation = validate_consolidation(
        synthesis_results=synthesis_results,
        source_dir=source_path,
        output_dir=output_path,
    )

    # Save synthesis log
    (output_path / '_consolidation_log.json').write_text(
        json.dumps({
            'consolidated_at': datetime.now().isoformat(),
            'source_directory': str(source_path),
            'output_directory': str(output_path),
            'strategy': strategy,
            'method': method,
            'threshold': threshold,
            'validation': validation,
            'results': synthesis_results
        }, indent=2, default=str)
    )

    return {
        'source_directory': str(source_path),
        'output_directory': str(output_path),
        'strategy': strategy,
        'method': method,
        'threshold': threshold,
        'files_analyzed': len(files),
        'clusters_created': len(clusters),
        'files_consolidated': validation['files_consolidated'],
        'files_created': validation['files_created'],
        'coverage': validation['coverage_percentage'],
        'validation': validation,
        'synthesis_results': synthesis_results,
        'non_markdown_copied': len(copied_files),
    }

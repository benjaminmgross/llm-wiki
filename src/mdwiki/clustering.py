"""
Cluster related markdown files for consolidation.
Supports topic-based, temporal, and hierarchical clustering methods.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Literal, TypedDict


class Cluster(TypedDict, total=False):
    """Type definition for a file cluster."""
    id: str
    method: str
    theme: str
    files: list[str]
    file_count: int
    primary_file: str
    avg_similarity: float
    directory: str
    start: str
    end: str
    duration_hours: float


ClusterMethod = Literal['topic', 'temporal', 'hierarchical', 'links', 'all']


def topic_cluster(
    *,
    relationships: dict[str, Any],
    threshold: float = 0.5,
) -> list[Cluster]:
    """
    Cluster files by content similarity.

    Parameters
    ----------
    relationships : dict[str, Any]
        Output from analyze_relationships.
    threshold : float, default=0.6
        Minimum similarity to link files.

    Returns
    -------
    list[Cluster]
        List of topic-based clusters.
    """
    similarities = relationships.get('content_similarities', [])

    adj: dict[str, set[str]] = defaultdict(set)
    for sim in similarities:
        if sim['similarity'] >= threshold:
            adj[sim['file1']].add(sim['file2'])
            adj[sim['file2']].add(sim['file1'])

    visited: set[str] = set()
    clusters: list[set[str]] = []

    def dfs(node: str, cluster: set[str]) -> None:
        if node in visited:
            return
        visited.add(node)
        cluster.add(node)
        for neighbor in adj.get(node, set()):
            dfs(neighbor, cluster)

    all_files: set[str] = set()
    for sim in similarities:
        all_files.add(sim['file1'])
        all_files.add(sim['file2'])

    for file in all_files:
        if file not in visited:
            cluster: set[str] = set()
            dfs(file, cluster)
            if cluster:
                clusters.append(cluster)

    result: list[Cluster] = []
    for i, files in enumerate(clusters):
        files_list = sorted(files)

        cluster_sims = [
            s for s in similarities
            if s['file1'] in files and s['file2'] in files
        ]
        avg_sim = (
            sum(s['similarity'] for s in cluster_sims) / len(cluster_sims)
            if cluster_sims else 0
        )

        file_times: list[tuple[str, float]] = []
        for f in files_list:
            try:
                mtime = Path(f).stat().st_mtime
                file_times.append((f, mtime))
            except Exception:
                file_times.append((f, 0))

        primary = max(file_times, key=lambda x: x[1])[0] if file_times else files_list[0]

        stems = [Path(f).stem for f in files_list]
        common_words = set(stems[0].lower().replace('-', ' ').replace('_', ' ').split())
        for stem in stems[1:]:
            words = set(stem.lower().replace('-', ' ').replace('_', ' ').split())
            common_words &= words
        theme = ' '.join(common_words) if common_words else stems[0]

        result.append({
            'id': f'cluster_{i+1:03d}',
            'method': 'topic',
            'theme': theme.title(),
            'files': files_list,
            'file_count': len(files_list),
            'primary_file': primary,
            'avg_similarity': round(avg_sim, 3)
        })

    return sorted(result, key=lambda x: x['file_count'], reverse=True)


def temporal_cluster(
    *,
    relationships: dict[str, Any],
    window_days: int = 7,
) -> list[Cluster]:
    """
    Cluster files by modification time proximity.

    Parameters
    ----------
    relationships : dict[str, Any]
        Output from analyze_relationships.
    window_days : int, default=7
        Time window in days.

    Returns
    -------
    list[Cluster]
        List of temporal clusters.
    """
    temporal_chains = relationships.get('temporal_chains', [])

    result: list[Cluster] = []
    for i, chain in enumerate(temporal_chains):
        files = chain['files']

        file_times: list[tuple[str, float]] = []
        for f in files:
            try:
                mtime = Path(f).stat().st_mtime
                file_times.append((f, mtime))
            except Exception:
                file_times.append((f, 0))

        primary = max(file_times, key=lambda x: x[1])[0] if file_times else files[0]

        result.append({
            'id': f'temporal_{i+1:03d}',
            'method': 'temporal',
            'theme': f"Modified {chain['start'][:10]}",
            'files': files,
            'file_count': len(files),
            'primary_file': primary,
            'start': chain['start'],
            'end': chain['end'],
            'duration_hours': chain['duration_hours']
        })

    return result


def hierarchical_cluster(*, relationships: dict[str, Any]) -> list[Cluster]:
    """
    Cluster files by directory structure combined with content similarity.

    Parameters
    ----------
    relationships : dict[str, Any]
        Output from analyze_relationships.

    Returns
    -------
    list[Cluster]
        List of directory-based clusters.
    """
    dir_groups: dict[str, list[str]] = defaultdict(list)

    all_files: set[str] = set()
    for sim in relationships.get('content_similarities', []):
        all_files.add(sim['file1'])
        all_files.add(sim['file2'])

    for f in all_files:
        parent = str(Path(f).parent)
        dir_groups[parent].append(f)

    result: list[Cluster] = []
    for i, (directory, files) in enumerate(sorted(dir_groups.items())):
        if len(files) < 2:
            continue

        file_times: list[tuple[str, float]] = []
        for f in files:
            try:
                mtime = Path(f).stat().st_mtime
                file_times.append((f, mtime))
            except Exception:
                file_times.append((f, 0))

        primary = max(file_times, key=lambda x: x[1])[0] if file_times else files[0]

        dir_sims = [
            s for s in relationships.get('content_similarities', [])
            if s['file1'] in files and s['file2'] in files
        ]
        avg_sim = (
            sum(s['similarity'] for s in dir_sims) / len(dir_sims)
            if dir_sims else 0
        )

        result.append({
            'id': f'dir_{i+1:03d}',
            'method': 'hierarchical',
            'theme': Path(directory).name or 'root',
            'directory': directory,
            'files': sorted(files),
            'file_count': len(files),
            'primary_file': primary,
            'avg_similarity': round(avg_sim, 3)
        })

    return sorted(result, key=lambda x: x['file_count'], reverse=True)


def link_cluster(*, relationships: dict[str, Any]) -> list[Cluster]:
    """
    Cluster files based on link relationships.

    Parameters
    ----------
    relationships : dict[str, Any]
        Output from analyze_relationships.

    Returns
    -------
    list[Cluster]
        List of link-based clusters.
    """
    link_clusters = relationships.get('link_relationships', {}).get('link_clusters', [])

    result: list[Cluster] = []
    for i, files in enumerate(link_clusters):
        if len(files) < 2:
            continue

        incoming = relationships['link_relationships']['incoming_links']
        link_counts = [(f, len(incoming.get(f, []))) for f in files]
        primary = max(link_counts, key=lambda x: x[1])[0]

        result.append({
            'id': f'links_{i+1:03d}',
            'method': 'links',
            'theme': Path(primary).stem.replace('-', ' ').replace('_', ' ').title(),
            'files': sorted(files),
            'file_count': len(files),
            'primary_file': primary
        })

    return result


def cluster_files(
    *,
    relationships: dict[str, Any],
    method: ClusterMethod = 'topic',
    threshold: float = 0.5,
    window_days: int = 7,
) -> list[Cluster]:
    """
    Cluster related files using the specified method.

    Parameters
    ----------
    relationships : dict[str, Any]
        Output from analyze_relationships.
    method : ClusterMethod, default='topic'
        Clustering method ('topic', 'temporal', 'hierarchical', 'links', 'all').
    threshold : float, default=0.6
        Similarity threshold for topic clustering.
    window_days : int, default=7
        Time window for temporal clustering.

    Returns
    -------
    list[Cluster]
        List of file clusters.
    """
    clusters: list[Cluster] = []

    if method in ('topic', 'all'):
        clusters.extend(topic_cluster(relationships=relationships, threshold=threshold))

    if method in ('temporal', 'all'):
        clusters.extend(temporal_cluster(relationships=relationships, window_days=window_days))

    if method in ('hierarchical', 'all'):
        clusters.extend(hierarchical_cluster(relationships=relationships))

    if method in ('links', 'all'):
        clusters.extend(link_cluster(relationships=relationships))

    return clusters

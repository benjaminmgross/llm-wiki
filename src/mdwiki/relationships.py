"""
Analyze relationships between markdown files.
Identifies semantic clusters, temporal chains, and content overlaps.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict


class Similarity(TypedDict):
    """Content similarity between two files."""
    file1: str
    file2: str
    similarity: float
    type: str


class Relationships(TypedDict):
    """Analysis results for file relationships."""
    content_similarities: list[Similarity]
    section_overlaps: list[dict[str, Any]]
    link_relationships: dict[str, Any]
    temporal_chains: list[dict[str, Any]]
    potential_conflicts: list[dict[str, Any]]


def tokenize(*, text: str) -> list[str]:
    """
    Simple tokenization for similarity comparison.

    Parameters
    ----------
    text : str
        Text to tokenize.

    Returns
    -------
    list[str]
        List of tokens with stopwords removed.
    """
    text = re.sub(r'[^\w\s]', ' ', text.lower())
    words = text.split()
    stopwords = {
        'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
        'of', 'with', 'by', 'from', 'is', 'are', 'was', 'were', 'be', 'been',
        'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
        'could', 'should', 'may', 'might', 'must', 'this', 'that', 'these',
        'those', 'it', 'its', 'as', 'if', 'then', 'than', 'so', 'such'
    }
    return [w for w in words if w not in stopwords and len(w) > 2]


def compute_tfidf(*, documents: list[list[str]]) -> tuple[dict[str, float], list[dict[str, float]]]:
    """
    Compute TF-IDF scores for documents.

    Parameters
    ----------
    documents : list[list[str]]
        List of tokenized documents.

    Returns
    -------
    tuple[dict[str, float], list[dict[str, float]]]
        Tuple of (IDF scores, list of TF-IDF vectors).
    """
    df: dict[str, int] = defaultdict(int)
    for doc in documents:
        for word in set(doc):
            df[word] += 1

    n_docs = len(documents)
    idf = {word: (n_docs / freq) for word, freq in df.items()}

    vectors: list[dict[str, float]] = []
    for doc in documents:
        tf: dict[str, int] = defaultdict(int)
        for word in doc:
            tf[word] += 1

        max_tf = max(tf.values()) if tf else 1
        vector = {
            word: (count / max_tf) * idf.get(word, 1)
            for word, count in tf.items()
        }
        vectors.append(vector)

    return idf, vectors


def cosine_similarity(*, vec1: dict[str, float], vec2: dict[str, float]) -> float:
    """
    Compute cosine similarity between two sparse vectors.

    Parameters
    ----------
    vec1 : dict[str, float]
        First sparse vector.
    vec2 : dict[str, float]
        Second sparse vector.

    Returns
    -------
    float
        Cosine similarity (0-1).
    """
    common_keys = set(vec1.keys()) & set(vec2.keys())
    if not common_keys:
        return 0.0

    dot_product = sum(vec1[k] * vec2[k] for k in common_keys)
    norm1 = sum(v ** 2 for v in vec1.values()) ** 0.5
    norm2 = sum(v ** 2 for v in vec2.values()) ** 0.5

    if norm1 == 0 or norm2 == 0:
        return 0.0

    return dot_product / (norm1 * norm2)


def analyze_content_similarity(
    *,
    inventory: dict[str, Any],
    threshold: float = 0.5,
) -> list[Similarity]:
    """
    Analyze content similarity between files.

    Parameters
    ----------
    inventory : dict[str, Any]
        Inventory from inventory_directory.
    threshold : float, default=0.3
        Minimum similarity to include.

    Returns
    -------
    list[Similarity]
        List of file pairs with similarity scores.
    """
    files = [f for f in inventory['files'] if 'error' not in f]

    documents: list[list[str]] = []
    for f in files:
        try:
            content = Path(f['path']).read_text(encoding='utf-8')
            tokens = tokenize(text=content)
            documents.append(tokens)
        except Exception:
            documents.append([])

    _, vectors = compute_tfidf(documents=documents)

    similarities: list[Similarity] = []
    for i in range(len(files)):
        for j in range(i + 1, len(files)):
            sim = cosine_similarity(vec1=vectors[i], vec2=vectors[j])
            if sim >= threshold:
                similarities.append({
                    'file1': files[i]['path'],
                    'file2': files[j]['path'],
                    'similarity': round(sim, 3),
                    'type': 'content'
                })

    return sorted(similarities, key=lambda x: x['similarity'], reverse=True)


def analyze_section_overlaps(*, inventory: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Find overlapping sections between files.

    Parameters
    ----------
    inventory : dict[str, Any]
        Inventory from inventory_directory.

    Returns
    -------
    list[dict[str, Any]]
        List of overlap records with type and occurrences.
    """
    files = [f for f in inventory['files'] if 'error' not in f]
    overlaps: list[dict[str, Any]] = []

    section_map: dict[str, list[dict[str, str]]] = defaultdict(list)
    for f in files:
        for section in f.get('sections', []):
            key = section['fingerprint']
            section_map[key].append({
                'file': f['path'],
                'heading': section['heading'],
                'lines': f"{section['start_line']}-{section['end_line']}"
            })

    for fingerprint, occurrences in section_map.items():
        if len(occurrences) > 1:
            overlaps.append({
                'type': 'exact_section',
                'fingerprint': fingerprint,
                'occurrences': occurrences
            })

    heading_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in files:
        for section in f.get('sections', []):
            heading_key = re.sub(r'[^\w\s]', '', section['heading'].lower())
            heading_map[heading_key].append({
                'file': f['path'],
                'heading': section['heading'],
                'lines': f"{section['start_line']}-{section['end_line']}",
                'word_count': section['word_count']
            })

    for heading, occurrences in heading_map.items():
        if len(occurrences) > 1:
            overlaps.append({
                'type': 'similar_heading',
                'heading': heading,
                'occurrences': occurrences
            })

    return overlaps


def analyze_link_relationships(*, inventory: dict[str, Any]) -> dict[str, Any]:
    """
    Analyze relationships based on internal links.

    Parameters
    ----------
    inventory : dict[str, Any]
        Inventory from inventory_directory.

    Returns
    -------
    dict[str, Any]
        Dictionary with outgoing_links, incoming_links, and link_clusters.
    """
    files = [f for f in inventory['files'] if 'error' not in f]

    outgoing: dict[str, set[str]] = defaultdict(set)
    incoming: dict[str, set[str]] = defaultdict(set)

    name_to_path: dict[str, str] = {}
    for f in files:
        name = Path(f['path']).stem
        name_to_path[name] = f['path']
        name_to_path[Path(f['path']).name] = f['path']

    for f in files:
        source = f['path']
        for link in f.get('links', {}).get('internal', []):
            link_name = link.split('/')[-1].replace('.md', '')
            target = name_to_path.get(link_name) or name_to_path.get(link)
            if target and target != source:
                outgoing[source].add(target)
                incoming[target].add(source)

    clusters: list[list[str]] = []
    visited: set[str] = set()

    def dfs(node: str, cluster: set[str]) -> None:
        if node in visited:
            return
        visited.add(node)
        cluster.add(node)
        for neighbor in outgoing.get(node, set()) | incoming.get(node, set()):
            dfs(neighbor, cluster)

    for f in files:
        if f['path'] not in visited:
            cluster: set[str] = set()
            dfs(f['path'], cluster)
            if len(cluster) > 1:
                clusters.append(list(cluster))

    return {
        'outgoing_links': {k: list(v) for k, v in outgoing.items()},
        'incoming_links': {k: list(v) for k, v in incoming.items()},
        'link_clusters': clusters
    }


def analyze_temporal_chains(
    *,
    inventory: dict[str, Any],
    window_hours: int = 168,
) -> list[dict[str, Any]]:
    """
    Find files that were modified close together.

    Parameters
    ----------
    inventory : dict[str, Any]
        Inventory from inventory_directory.
    window_hours : int, default=168
        Time window in hours (default 7 days).

    Returns
    -------
    list[dict[str, Any]]
        List of temporal chains with files, start, end, duration.
    """
    files = [f for f in inventory['files'] if 'error' not in f]

    file_times: list[tuple[str, datetime, str]] = []
    for f in files:
        try:
            ts = datetime.fromisoformat(f['modified_fs'].replace('Z', '+00:00'))
            file_times.append((f['path'], ts, f.get('fingerprint', '')))
        except Exception:
            pass

    file_times.sort(key=lambda x: x[1])

    chains: list[dict[str, Any]] = []
    i = 0
    while i < len(file_times):
        chain = [file_times[i]]
        j = i + 1
        while j < len(file_times):
            time_diff = (file_times[j][1] - chain[-1][1]).total_seconds() / 3600
            if time_diff <= window_hours:
                chain.append(file_times[j])
                j += 1
            else:
                break

        if len(chain) > 1:
            chains.append({
                'files': [c[0] for c in chain],
                'start': chain[0][1].isoformat(),
                'end': chain[-1][1].isoformat(),
                'duration_hours': round(
                    (chain[-1][1] - chain[0][1]).total_seconds() / 3600, 1
                )
            })

        i = j if j > i + 1 else i + 1

    return chains


def detect_conflicts(
    *,
    inventory: dict[str, Any],
    similarities: list[Similarity],
) -> list[dict[str, Any]]:
    """
    Detect potential content conflicts in similar files.

    Parameters
    ----------
    inventory : dict[str, Any]
        Inventory from inventory_directory.
    similarities : list[Similarity]
        List of similarity pairs from analyze_content_similarity.

    Returns
    -------
    list[dict[str, Any]]
        List of conflict records with type, files, and differences.
    """
    conflicts: list[dict[str, Any]] = []

    for sim in similarities:
        if sim['similarity'] < 0.5:
            continue

        try:
            content1 = Path(sim['file1']).read_text(encoding='utf-8')
            content2 = Path(sim['file2']).read_text(encoding='utf-8')
        except Exception:
            continue

        nums1 = set(re.findall(r'\b\d+(?:\.\d+)?\b', content1))
        nums2 = set(re.findall(r'\b\d+(?:\.\d+)?\b', content2))

        if nums1 != nums2:
            diff_nums = (nums1 - nums2) | (nums2 - nums1)
            if diff_nums:
                conflicts.append({
                    'type': 'numeric_difference',
                    'file1': sim['file1'],
                    'file2': sim['file2'],
                    'similarity': sim['similarity'],
                    'different_values': list(diff_nums)[:10]
                })

    return conflicts


def analyze_relationships(
    *,
    inventory: dict[str, Any],
    threshold: float = 0.5,
) -> Relationships:
    """
    Perform full relationship analysis on inventory.

    Parameters
    ----------
    inventory : dict[str, Any]
        Output from inventory_directory.
    threshold : float, default=0.3
        Similarity threshold for content comparison.

    Returns
    -------
    Relationships
        Complete relationships analysis with similarities, overlaps, links, etc.
    """
    similarities = analyze_content_similarity(inventory=inventory, threshold=threshold)
    overlaps = analyze_section_overlaps(inventory=inventory)
    link_relations = analyze_link_relationships(inventory=inventory)
    temporal = analyze_temporal_chains(inventory=inventory)
    conflicts = detect_conflicts(inventory=inventory, similarities=similarities)

    return {
        'content_similarities': similarities,
        'section_overlaps': overlaps,
        'link_relationships': link_relations,
        'temporal_chains': temporal,
        'potential_conflicts': conflicts
    }

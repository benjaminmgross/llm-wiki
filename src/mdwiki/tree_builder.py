"""
Build hierarchical document tree from clustered sections.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial.distance import pdist


class TreeBuilder:
    """Build document hierarchy from embedded sections."""

    def __init__(
        self,
        threshold: float = 0.5,
        min_sections_per_doc: int = 1,
        duplicate_similarity: float = 0.9,
    ):
        """
        Initialize tree builder.

        Parameters
        ----------
        threshold : float
            Distance threshold for clustering (lower = more clusters).
        min_sections_per_doc : int
            Minimum sections to form a document.
        duplicate_similarity : float
            Cosine similarity threshold for duplicate detection.
        """
        self.threshold = threshold
        self.min_sections_per_doc = min_sections_per_doc
        self.duplicate_similarity = duplicate_similarity

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        a_arr, b_arr = np.array(a), np.array(b)
        return float(np.dot(a_arr, b_arr) / (np.linalg.norm(a_arr) * np.linalg.norm(b_arr) + 1e-10))

    def _mark_duplicates(self, sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Mark duplicate sections based on fingerprint and embedding similarity."""
        # Group by fingerprint
        fingerprint_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for s in sections:
            fingerprint_groups[s['fingerprint']].append(s)

        # For each group with duplicates, keep newest
        for _fp, group in fingerprint_groups.items():
            if len(group) <= 1:
                continue

            # Sort by modification time, newest first
            sorted_group = sorted(group, key=lambda x: x.get('last_modified', ''), reverse=True)
            newest = sorted_group[0]

            for older in sorted_group[1:]:
                older['duplicate_of'] = newest['section_id']

        # Also check embedding similarity for near-duplicates
        for i, s1 in enumerate(sections):
            if s1.get('duplicate_of'):
                continue
            for s2 in sections[i + 1:]:
                if s2.get('duplicate_of'):
                    continue
                if s1['fingerprint'] == s2['fingerprint']:
                    continue  # Already handled

                sim = self._cosine_similarity(s1['embedding'], s2['embedding'])
                if sim >= self.duplicate_similarity:
                    # Mark older as duplicate
                    if s1.get('last_modified', '') >= s2.get('last_modified', ''):
                        s2['duplicate_of'] = s1['section_id']
                    else:
                        s1['duplicate_of'] = s2['section_id']

        return sections

    def _generate_doc_name(self, sections: list[dict[str, Any]]) -> str:
        """Generate document name from section keywords."""
        all_keywords: list[str] = []
        for s in sections:
            all_keywords.extend(s.get('keywords', [])[:3])

        if not all_keywords:
            return "untitled.md"

        # Get most common keywords
        keyword_counts: dict[str, int] = defaultdict(int)
        for kw in all_keywords:
            keyword_counts[kw.lower().replace(' ', '-')] += 1

        top_keywords = sorted(keyword_counts.items(), key=lambda x: -x[1])[:2]
        name = '-'.join(kw for kw, _ in top_keywords)
        return f"{name}.md" if name else "untitled.md"

    def _compute_confidence(self, sections: list[dict[str, Any]]) -> float:
        """Compute cluster confidence as average pairwise similarity."""
        if len(sections) <= 1:
            return 1.0

        similarities = []
        for i, s1 in enumerate(sections):
            for s2 in sections[i + 1:]:
                sim = self._cosine_similarity(s1['embedding'], s2['embedding'])
                similarities.append(sim)

        return float(np.mean(similarities)) if similarities else 1.0

    def build_hierarchy(self, sections: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Build hierarchical document tree from sections.

        Parameters
        ----------
        sections : list[dict]
            Sections with embeddings, keywords, fingerprints.

        Returns
        -------
        dict
            Hierarchy with themes, documents, sections, orphans.
        """
        if not sections:
            return {'themes': [], 'orphans': []}

        # Mark duplicates first
        sections = self._mark_duplicates(sections)

        if len(sections) == 1:
            return {
                'themes': [{
                    'theme': sections[0].get('heading', 'Untitled'),
                    'confidence': 1.0,
                    'documents': [{
                        'name': self._generate_doc_name(sections),
                        'sections': self._format_sections(sections),
                    }]
                }],
                'orphans': []
            }

        # Extract embeddings
        embeddings = np.array([s['embedding'] for s in sections])

        # Compute distance matrix (1 - cosine similarity)
        distances = pdist(embeddings, metric='cosine')

        # Hierarchical clustering
        Z = linkage(distances, method='ward')

        # Dynamic cut based on threshold
        # Convert threshold to distance (1 - similarity)
        distance_threshold = 1 - self.threshold
        clusters = fcluster(Z, t=distance_threshold, criterion='distance')

        # Group sections by cluster
        cluster_groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for section, cluster_id in zip(sections, clusters, strict=True):
            cluster_groups[cluster_id].append(section)

        # Build themes (each cluster becomes a theme with one or more documents)
        themes = []
        orphans = []

        for _cluster_id, group in cluster_groups.items():
            if len(group) < self.min_sections_per_doc:
                # Too small, mark as orphan
                for s in group:
                    orphans.append({
                        **self._format_section(s),
                        'suggested_action': 'standalone' if len(s.get('content', '')) > 200 else 'exclude'
                    })
                continue

            # Generate theme name from top keywords
            theme_name = self._generate_theme_name(group)
            confidence = self._compute_confidence(group)

            # For now, each theme has one document
            # Future: sub-cluster large themes into multiple documents
            themes.append({
                'theme': theme_name,
                'confidence': round(confidence, 2),
                'documents': [{
                    'name': self._generate_doc_name(group),
                    'sections': self._format_sections(group),
                }]
            })

        return {
            'themes': sorted(themes, key=lambda x: -x['confidence']),
            'orphans': orphans,
        }

    def _generate_theme_name(self, sections: list[dict[str, Any]]) -> str:
        """Generate theme name from section headings/keywords."""
        # Find common words in headings
        words: list[str] = []
        for s in sections:
            heading_words = s.get('heading', '').split()
            words.extend(w.lower() for w in heading_words if len(w) > 3)

        if not words:
            return "Miscellaneous"

        word_counts: dict[str, int] = defaultdict(int)
        for w in words:
            word_counts[w] += 1

        top_word = max(word_counts.items(), key=lambda x: x[1])[0]
        return top_word.title()

    def _format_section(self, section: dict[str, Any]) -> dict[str, Any]:
        """Format section for manifest output."""
        result: dict[str, Any] = {
            'id': section['section_id'],
            'heading': section['heading'],
            'summary': section.get('summary'),
            'keywords': section.get('keywords', []),
            'source': section.get('source_file', '').split('/')[-1],
            'modified': section.get('last_modified', '')[:10],
        }
        if section.get('duplicate_of'):
            result['duplicate_of'] = section['duplicate_of']
        if section.get('encapsulation_score') is not None:
            result['encapsulation_score'] = round(section['encapsulation_score'], 2)
        return result

    def _format_sections(self, sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Format multiple sections for manifest."""
        return [self._format_section(s) for s in sections]

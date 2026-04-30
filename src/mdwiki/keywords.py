"""
Extract keywords from sections using TF-IDF.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer


class KeywordExtractor:
    """Extract keywords using TF-IDF."""

    def __init__(
        self,
        max_keywords: int = 8,
        min_df: int = 1,
        max_df: float = 0.8,
    ):
        """
        Initialize keyword extractor.

        Parameters
        ----------
        max_keywords : int
            Maximum keywords per section.
        min_df : int
            Minimum document frequency.
        max_df : float
            Maximum document frequency.
        """
        self.max_keywords = max_keywords
        self.vectorizer = TfidfVectorizer(
            max_features=500,
            stop_words="english",
            min_df=min_df,
            max_df=max_df,
            ngram_range=(1, 2),
        )

    def extract_keywords(self, sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Extract keywords for all sections.

        Parameters
        ----------
        sections : list[dict]
            List of section dicts with 'content' key.

        Returns
        -------
        list[dict]
            Same sections with 'keywords' key added.
        """
        if not sections:
            return sections

        texts = [s['content'] for s in sections]

        try:
            tfidf_matrix = self.vectorizer.fit_transform(texts)
            feature_names = self.vectorizer.get_feature_names_out()
        except ValueError:
            # Not enough documents or vocabulary
            for section in sections:
                section['keywords'] = []
            return sections

        for i, section in enumerate(sections):
            scores = tfidf_matrix[i].toarray().flatten()
            top_indices = np.argsort(scores)[-self.max_keywords:][::-1]
            keywords = [feature_names[idx] for idx in top_indices if scores[idx] > 0]
            section['keywords'] = keywords

        return sections

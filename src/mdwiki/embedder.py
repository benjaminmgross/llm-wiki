"""
Generate embeddings for sections using sentence-transformers.
"""

from __future__ import annotations

from typing import Any

from sentence_transformers import SentenceTransformer


class Embedder:
    """Generate embeddings for text sections."""

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        """
        Initialize embedder with specified model.

        Parameters
        ----------
        model_name : str
            HuggingFace model name for sentence-transformers.
        """
        self.model_name = model_name
        self.model = SentenceTransformer(model_name)
        self.dimension = self.model.get_sentence_embedding_dimension()

    def embed_sections(self, sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Add embeddings to sections.

        Parameters
        ----------
        sections : list[dict]
            List of section dicts with 'content' key.

        Returns
        -------
        list[dict]
            Same sections with 'embedding' key added.
        """
        if not sections:
            return sections

        texts = [s['content'] for s in sections]
        embeddings = self.model.encode(
            texts,
            batch_size=32,
            show_progress_bar=False,
            convert_to_numpy=True,
        )

        for section, embedding in zip(sections, embeddings, strict=True):
            section['embedding'] = embedding.tolist()

        return sections

    def embed_text(self, text: str) -> list[float]:
        """
        Generate embedding for a single text string.

        Parameters
        ----------
        text : str
            Text to embed.

        Returns
        -------
        list[float]
            Embedding vector.
        """
        embedding = self.model.encode(text, convert_to_numpy=True)
        return embedding.tolist()

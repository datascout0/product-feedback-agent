"""Local embedding generation using Sentence Transformers.

Design notes:
- The model is loaded lazily on first use, so importing this module (or
  constructing a LocalEmbedder) never triggers a model download/load.
- Embeddings are L2-normalized so that cosine similarity in Chroma reduces
  to a dot product, matching the "cosine" hnsw:space configured on our
  collections.
"""

from __future__ import annotations

DEFAULT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


class LocalEmbedder:
    """Generates normalized embeddings with a local Sentence Transformers model."""

    def __init__(self, model_name: str = DEFAULT_MODEL_NAME) -> None:
        self.model_name = model_name
        self._model = None

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of documents, returning one normalized vector per text."""
        model = self._get_model()
        embeddings = model.encode(list(texts), normalize_embeddings=True)
        return [embedding.tolist() for embedding in embeddings]

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query string, returning a normalized vector."""
        model = self._get_model()
        embedding = model.encode(text, normalize_embeddings=True)
        return embedding.tolist()

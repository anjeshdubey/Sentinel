"""Embedder protocol and BGE-small-en-v1.5 implementation."""

from typing import Protocol, runtime_checkable

try:
    import numpy as np
except ImportError as _e:
    raise ImportError(
        "The 'numpy' package is required for RAG features. "
        "Install with: pip install sentinel[rag]"
    ) from _e


@runtime_checkable
class Embedder(Protocol):
    """Protocol for text embedding models.

    Implementations must expose `dim` (vector dimensionality) and
    `embed(texts)` which returns a normalized numpy array of shape (N, dim).
    """

    dim: int

    def embed(self, texts: list[str]) -> np.ndarray:
        """Embed a list of texts into normalized vectors.

        Args:
            texts: List of strings to embed.

        Returns:
            numpy array of shape (len(texts), self.dim) with L2-normalized rows.
        """
        ...


class BgeSmallEmbedder:
    """BGE-small-en-v1.5 via sentence-transformers, CPU-only default.

    This is the project's default embedding model (384 dims, 33M params).
    It runs on CPU with ~20ms/query latency for single queries.
    """

    dim: int = 384

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise ImportError(
                "The 'sentence-transformers' package is required for RAG features. "
                "Install with: pip install sentinel[rag]"
            ) from e

        self._model = SentenceTransformer(model_name)
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        """Return the model identifier for consistency checks."""
        return self._model_name

    def embed(self, texts: list[str]) -> np.ndarray:
        """Embed texts using BGE-small with L2 normalization.

        Args:
            texts: List of strings to embed.

        Returns:
            numpy array of shape (len(texts), 384) with normalized rows.
        """
        embeddings = self._model.encode(texts, normalize_embeddings=True)
        return np.asarray(embeddings, dtype=np.float32)

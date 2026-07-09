"""Bootstrap functions for building retrieval components."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sentinel.retrieval.embedder import BgeSmallEmbedder
from sentinel.retrieval.retriever import RunbookRetriever
from sentinel.retrieval.store import QdrantStore

if TYPE_CHECKING:
    from sentinel.config import Settings


def build_retriever(settings: Settings) -> RunbookRetriever:
    """Build configured RunbookRetriever from settings.

    Args:
        settings: Application settings containing Qdrant and retrieval config.

    Returns:
        Configured RunbookRetriever ready for use.

    Example:
        >>> from sentinel.config import load_settings
        >>> settings = load_settings()
        >>> retriever = build_retriever(settings)
        >>> results = retriever.query("checkout service 503 errors")
    """
    store = QdrantStore(
        mode=settings.qdrant.mode,
        path=settings.qdrant.path,
        url=settings.qdrant.url,
        embedding_model=settings.retrieval.embedding_model,
        embedding_dim=settings.retrieval.embedding_dim,
    )

    embedder = BgeSmallEmbedder(model_name=settings.retrieval.embedding_model)

    return RunbookRetriever(
        store=store,
        embedder=embedder,
        collection_name=settings.qdrant.runbook_collection,
        top_k=settings.retrieval.top_k,
        score_threshold=settings.retrieval.score_threshold,
    )

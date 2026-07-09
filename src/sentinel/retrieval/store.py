"""QdrantStore wrapper — supports both embedded (path=) and server (url=) modes."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Literal

try:
    import numpy as np
except ImportError as _e:
    raise ImportError(
        "The 'numpy' package is required for RAG features. "
        "Install with: pip install sentinel[rag]"
    ) from _e

from pydantic import BaseModel

logger = logging.getLogger(__name__)


@dataclass
class ScoredPoint:
    """Wrapper for a scored result from Qdrant vector search."""

    id: str | int
    score: float
    payload: dict[str, Any]

    def payload_as(self, model_cls: type[BaseModel]) -> BaseModel:
        """Deserialize payload into a Pydantic model."""
        return model_cls.model_validate(self.payload)


class QdrantStore:
    """Unified Qdrant wrapper supporting embedded (path=) and server (url=) modes.

    Invariant Q1.a: On startup, verifies all collections use the same
    embedding model. Refuses to serve if a mismatch is detected.

    Usage:
        store = QdrantStore(mode="embedded", path="./data/qdrant", embedding_model="BAAI/bge-small-en-v1.5")
        store.ensure_collection("runbooks_v1", dim=384)
        results = store.search("runbooks_v1", vector, k=10)
    """

    def __init__(
        self,
        mode: Literal["embedded", "server"] = "embedded",
        path: str = "./data/qdrant",
        url: str = "http://localhost:6333",
        embedding_model: str = "BAAI/bge-small-en-v1.5",
        embedding_dim: int = 384,
    ) -> None:
        try:
            from qdrant_client import QdrantClient
        except ImportError as e:
            raise ImportError(
                "The 'qdrant-client' package is required for RAG features. "
                "Install with: pip install sentinel[rag]"
            ) from e

        self._mode = mode
        self._embedding_model = embedding_model
        self._embedding_dim = embedding_dim

        if mode == "embedded":
            self._client = QdrantClient(path=path)
            logger.info("QdrantStore initialized in embedded mode at %s", path)
        else:
            self._client = QdrantClient(url=url)
            logger.info("QdrantStore initialized in server mode at %s", url)

        # Verify model_id consistency across existing collections (invariant Q1.a)
        self._verify_model_consistency()

    @property
    def client(self) -> Any:
        """Access the underlying QdrantClient for advanced operations."""
        return self._client

    def _verify_model_consistency(self) -> None:
        """Check that all existing collections use the same embedding model.

        Reads model_id from collection metadata. Refuses to serve if any
        collection's stored model_id doesn't match the configured model.
        """
        from qdrant_client.http.exceptions import UnexpectedResponse

        try:
            collections = self._client.get_collections().collections
        except (UnexpectedResponse, Exception) as e:
            logger.warning("Could not verify collection consistency: %s", e)
            return

        for collection in collections:
            name = collection.name
            try:
                info = self._client.get_collection(name)
                # Check if model_id is stored in collection metadata
                # We store it as a payload field in a special metadata point
                results = self._client.scroll(
                    collection_name=name,
                    scroll_filter={
                        "must": [{"key": "_meta_type", "match": {"value": "model_info"}}]
                    },
                    limit=1,
                )
                points = results[0] if results else []
                if points:
                    stored_model = points[0].payload.get("model_id")
                    if stored_model and stored_model != self._embedding_model:
                        msg = (
                            f"Collection '{name}' was built with model "
                            f"'{stored_model}' but current config uses "
                            f"'{self._embedding_model}'. Rebuild required."
                        )
                        raise RuntimeError(msg)
            except (UnexpectedResponse, RuntimeError):
                raise
            except Exception as e:
                logger.debug("Skipping consistency check for %s: %s", name, e)

    def ensure_collection(self, name: str, dim: int | None = None) -> None:
        """Create collection if it doesn't exist, with proper config.

        Creates payload indexes on service_tags and environment.
        Stores model_id metadata for invariant Q1.a.
        """
        from qdrant_client.http.exceptions import UnexpectedResponse
        from qdrant_client.models import Distance, PointStruct, VectorParams

        dim = dim or self._embedding_dim

        try:
            self._client.get_collection(name)
            logger.debug("Collection '%s' already exists", name)
        except (UnexpectedResponse, Exception):
            self._client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )
            logger.info("Created collection '%s' (dim=%d, cosine)", name, dim)

            # Store model_id metadata for consistency checks.
            # Use a fixed UUID string ID to avoid collision with integer content IDs.
            self._client.upsert(
                collection_name=name,
                points=[
                    PointStruct(
                        id="00000000-0000-0000-0000-000000000000",
                        vector=[0.0] * dim,
                        payload={
                            "_meta_type": "model_info",
                            "model_id": self._embedding_model,
                        },
                    )
                ],
            )

        # Create payload indexes for filter performance
        self._create_payload_indexes(name)

    def _create_payload_indexes(self, collection_name: str) -> None:
        """Create payload indexes on service_tags and environment."""
        from qdrant_client.models import PayloadSchemaType

        try:
            self._client.create_payload_index(
                collection_name=collection_name,
                field_name="service_tags",
                field_schema=PayloadSchemaType.KEYWORD,
            )
            logger.info(
                "Payload index on 'service_tags' registered for '%s'",
                collection_name,
            )
        except Exception as e:
            logger.debug("service_tags index may already exist: %s", e)

        try:
            self._client.create_payload_index(
                collection_name=collection_name,
                field_name="environment",
                field_schema=PayloadSchemaType.KEYWORD,
            )
            logger.info(
                "Payload index on 'environment' registered for '%s'",
                collection_name,
            )
        except Exception as e:
            logger.debug("environment index may already exist: %s", e)

    def search(
        self,
        collection_name: str,
        vector: np.ndarray,
        k: int = 10,
        filter_dict: dict[str, Any] | None = None,
    ) -> list[ScoredPoint]:
        """Synchronous vector search with optional payload filter.

        Filter strategy (per design spec):
        - 'environment' is a hard filter (must match) — ensures results from
          wrong environments never appear.
        - 'service_tags' is a soft-boost filter (should) — prefers matching
          services but does not exclude others.

        If the combined search (must + should) returns zero results, retries
        with only the must portion (hard filter without soft boost).

        Args:
            collection_name: Target collection.
            vector: Query vector (1D numpy array).
            k: Number of results to return.
            filter_dict: Optional Qdrant filter dict with keys like
                'environment' (hard) and 'service_tags' (soft-boost).

        Returns:
            List of ScoredPoint results sorted by descending score.
        """
        from qdrant_client.models import Filter, FieldCondition, MatchAny, MatchValue

        # Exclude metadata points from search results
        must_not_conditions = [
            FieldCondition(key="_meta_type", match=MatchValue(value="model_info"))
        ]

        must_conditions: list[FieldCondition] = []
        should_conditions: list[FieldCondition] = []

        if filter_dict:
            for key, value in filter_dict.items():
                if key == "environment":
                    # Environment is a HARD filter (must match)
                    if isinstance(value, list):
                        must_conditions.append(
                            FieldCondition(key=key, match=MatchAny(any=value))
                        )
                    else:
                        must_conditions.append(
                            FieldCondition(key=key, match=MatchValue(value=value))
                        )
                else:
                    # service_tags and other fields use soft-boost (should)
                    if isinstance(value, list):
                        should_conditions.append(
                            FieldCondition(key=key, match=MatchAny(any=value))
                        )
                    else:
                        should_conditions.append(
                            FieldCondition(key=key, match=MatchValue(value=value))
                        )

        # Build combined filter with must (hard) + should (soft-boost)
        combined_filter = Filter(
            must=must_conditions or None,
            should=should_conditions or None,
            must_not=must_not_conditions,
        )

        results = self._client.query_points(
            collection_name=collection_name,
            query=vector.tolist(),
            limit=k,
            query_filter=combined_filter,
        )

        # Soft-boost fallback: if zero results with should conditions, retry without them
        if not results.points and should_conditions:
            logger.debug(
                "Zero results with soft-boost filter — retrying with hard filter only"
            )
            fallback_filter = Filter(
                must=must_conditions or None,
                must_not=must_not_conditions,
            )
            results = self._client.query_points(
                collection_name=collection_name,
                query=vector.tolist(),
                limit=k,
                query_filter=fallback_filter,
            )

        return [
            ScoredPoint(
                id=point.id,
                score=point.score,
                payload=point.payload or {},
            )
            for point in results.points
        ]

    async def asearch(
        self,
        collection_name: str,
        vector: np.ndarray,
        k: int = 10,
        filter_dict: dict[str, Any] | None = None,
    ) -> list[ScoredPoint]:
        """Async vector search — runs sync search in executor.

        Args:
            collection_name: Target collection.
            vector: Query vector (1D numpy array).
            k: Number of results to return.
            filter_dict: Optional Qdrant filter dict.

        Returns:
            List of ScoredPoint results sorted by descending score.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.search(collection_name, vector, k, filter_dict),
        )

    def upsert_points(
        self,
        collection_name: str,
        ids: list[int | str],
        vectors: np.ndarray,
        payloads: list[dict[str, Any]],
    ) -> None:
        """Upsert points into a collection.

        Args:
            collection_name: Target collection.
            ids: Point IDs.
            vectors: Numpy array of shape (N, dim).
            payloads: List of payload dicts, one per point.
        """
        from qdrant_client.models import PointStruct

        points = [
            PointStruct(
                id=point_id,
                vector=vec.tolist(),
                payload=payload,
            )
            for point_id, vec, payload in zip(ids, vectors, payloads)
        ]
        self._client.upsert(collection_name=collection_name, points=points)

    def count(self, collection_name: str) -> int:
        """Return the number of points in a collection (excluding metadata)."""
        info = self._client.get_collection(collection_name)
        # Subtract 1 for the metadata point
        return max(0, info.points_count - 1) if info.points_count else 0

    def scroll_all(
        self,
        collection_name: str,
        filter_dict: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[ScoredPoint]:
        """Scroll through all points matching a filter.

        Args:
            collection_name: Target collection.
            filter_dict: Optional filter.
            limit: Max points to return.

        Returns:
            List of ScoredPoint (score=0 since scroll doesn't score).
        """
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        scroll_filter = None
        if filter_dict:
            conditions = []
            for key, value in filter_dict.items():
                conditions.append(
                    FieldCondition(key=key, match=MatchValue(value=value))
                )
            scroll_filter = Filter(must=conditions)

        results, _ = self._client.scroll(
            collection_name=collection_name,
            scroll_filter=scroll_filter,
            limit=limit,
        )
        return [
            ScoredPoint(
                id=point.id,
                score=0.0,
                payload=point.payload or {},
            )
            for point in results
        ]

"""Qdrant-backed PastIncidentsProvider — reads the incidents_v1 collection.

IncidentIndexer (sentinel.retrieval.indexer) owns all writes to incidents_v1.
This provider is read-only against that collection, per design section 2.6.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from sentinel.tools.errors import ToolBackendError
from sentinel.tools.models import PastIncident

logger = logging.getLogger(__name__)

# Cosine score -> relevance bucket thresholds (mirrors PastIncident docstring).
_STRONG_THRESHOLD = 0.75
_MODERATE_THRESHOLD = 0.50


def _bucket_relevance(score: float) -> str:
    if score >= _STRONG_THRESHOLD:
        return "strong"
    if score >= _MODERATE_THRESHOLD:
        return "moderate"
    return "weak"


def _parse_occurred_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now()


class AsyncQdrantPastIncidentsProvider:
    """Finds semantically similar past incidents via vector search.

    If `client` is None (Qdrant not configured), get_similar_past_incidents
    returns an empty tuple instead of raising — the demo should still run
    without a wired vector store for this particular tool.
    """

    def __init__(
        self,
        client: Any,
        collection_name: str = "incidents_v1",
        embedder: Any = None,
    ) -> None:
        self._client = client
        self._collection_name = collection_name
        self._embedder = embedder

    async def get_similar_past_incidents(
        self, query_text: str, service: str | None = None, k: int = 3
    ) -> list[PastIncident]:
        if self._client is None or self._embedder is None:
            return []

        try:
            loop = asyncio.get_running_loop()
            vector = await loop.run_in_executor(
                None, lambda: self._embedder.embed([query_text])[0]
            )

            filter_dict: dict[str, Any] | None = None
            if service:
                filter_dict = {"service_tags": service}

            results = await self._client.asearch(
                collection_name=self._collection_name,
                vector=vector,
                k=k,
                filter_dict=filter_dict,
            )

            if not results and filter_dict:
                results = await self._client.asearch(
                    collection_name=self._collection_name,
                    vector=vector,
                    k=k,
                    filter_dict=None,
                )
        except Exception as e:
            raise ToolBackendError("past_incidents", str(e)) from e

        incidents: list[PastIncident] = []
        for rank, point in enumerate(results[:k], start=1):
            payload = point.payload
            incidents.append(
                PastIncident(
                    incident_id=payload.get("incident_id", ""),
                    title=payload.get("title", ""),
                    service=payload.get("service", "unknown"),
                    severity=payload.get("severity", "medium"),
                    resolved_summary=payload.get("resolved_summary", ""),
                    occurred_at=_parse_occurred_at(payload.get("occurred_at")),
                    rank=rank,
                    relevance=_bucket_relevance(point.score),
                )
            )
        return incidents

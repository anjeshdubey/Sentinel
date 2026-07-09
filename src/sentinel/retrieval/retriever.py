"""RunbookRetriever — vector search with soft-boost filter and score threshold."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from sentinel.retrieval.embedder import Embedder
from sentinel.retrieval.models import RetrievalResult, RetrievedChunk, RunbookChunk
from sentinel.retrieval.store import QdrantStore

if TYPE_CHECKING:
    from sentinel.observability.trace import TraceCollector

logger = logging.getLogger(__name__)

# Approximate tokens per character for estimating injected token cost
_CHARS_PER_TOKEN = 4.0


def _estimate_tokens(text: str) -> int:
    """Estimate token count from text length."""
    return max(1, int(len(text) / _CHARS_PER_TOKEN))


class RunbookRetriever:
    """Retrieves relevant runbook chunks via vector search with filtering.

    Provides both async (.aquery()) and sync (.query()) interfaces.
    Async-first design for LangGraph/FastAPI compatibility.

    Filter strategy: soft boost with hard-filter fallback.
    If service_filter is provided, apply it as a Qdrant 'should' clause
    (score boost). If zero results return, retry WITHOUT the filter
    (fallback to global search).
    """

    def __init__(
        self,
        store: QdrantStore,
        embedder: Embedder,
        collection_name: str = "runbooks_v1",
        top_k: int = 3,
        fetch_k: int = 10,
        score_threshold: float = 0.35,
    ) -> None:
        """Initialize the retriever.

        Args:
            store: QdrantStore instance (embedded or server mode).
            embedder: Embedder implementation (e.g., BgeSmallEmbedder).
            collection_name: Qdrant collection to search.
            top_k: Number of top results to return after filtering.
            fetch_k: Number of candidates to fetch from vector search.
            score_threshold: Minimum cosine similarity score (below = no match).
        """
        self._store = store
        self._embedder = embedder
        self._collection_name = collection_name
        self._top_k = top_k
        self._fetch_k = fetch_k
        self._score_threshold = score_threshold

    async def aquery(
        self,
        text: str,
        service_filter: str | None = None,
        environment_filter: str | None = None,
    ) -> RetrievalResult:
        """Async retrieval (primary interface for LangGraph, FastAPI, M5 workers).

        Args:
            text: Query text (built from alert fields, max 600 chars).
            service_filter: Optional service name for soft-boost filter.
            environment_filter: Optional environment for filter.

        Returns:
            RetrievalResult with top-K chunks, token estimate, and latency.
        """
        start_ms = time.perf_counter_ns() // 1_000_000

        # Embed query — CPU-bound, run in executor to avoid blocking event loop
        loop = asyncio.get_running_loop()
        vec = await loop.run_in_executor(
            None, lambda: self._embedder.embed([text])[0]
        )

        # Build filter dict for soft-boost
        filter_dict = self._build_filter(service_filter, environment_filter)

        # Vector search with filter
        raw = await self._store.asearch(
            collection_name=self._collection_name,
            vector=vec,
            k=self._fetch_k,
            filter_dict=filter_dict,
        )

        # Hard-filter fallback: if zero results and filter was applied, retry without
        if not raw and filter_dict:
            logger.debug(
                "Zero results with filter %s — retrying without filter",
                filter_dict,
            )
            raw = await self._store.asearch(
                collection_name=self._collection_name,
                vector=vec,
                k=self._fetch_k,
                filter_dict=None,
            )

        # Score threshold filtering
        candidates = [
            RetrievedChunk(
                chunk=RunbookChunk.model_validate(r.payload),
                score=r.score,
                source="vector",
            )
            for r in raw
            if r.score >= self._score_threshold
        ]

        # Take top-K
        candidates = candidates[: self._top_k]

        # Calculate latency
        end_ms = time.perf_counter_ns() // 1_000_000
        latency_ms = end_ms - start_ms

        return RetrievalResult(
            query=text,
            chunks=candidates,
            total_tokens=sum(_estimate_tokens(c.chunk.text) for c in candidates),
            latency_ms=latency_ms,
        )

    def query(
        self,
        text: str,
        service_filter: str | None = None,
        environment_filter: str | None = None,
        collector: TraceCollector | None = None,
    ) -> RetrievalResult:
        """Sync facade for CLI and non-async callers.

        Uses asyncio.run() when no event loop is running. If called from
        within an existing async context (e.g., FastAPI endpoint), falls
        back to running the coroutine on the already-running loop via a
        helper thread to avoid 'RuntimeError: This event loop is already
        running'.

        Args:
            text: Query text.
            service_filter: Optional service name filter.
            environment_filter: Optional environment filter.
            collector: Optional TraceCollector for event emission.

        Returns:
            RetrievalResult with top-K chunks.
        """
        if collector:
            from sentinel.observability.trace import EventType
            collector.emit(EventType.RAG_QUERY_STARTED, query=text[:100], service_filter=service_filter)

        coro = self.aquery(text, service_filter, environment_filter)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            result = asyncio.run(coro)
            if collector:
                from sentinel.observability.trace import EventType
                collector.emit(
                    EventType.RAG_QUERY_COMPLETED,
                    chunks_returned=len(result.chunks),
                    latency_ms=result.latency_ms,
                )
            return result

        # Already inside an async context — run in a separate thread
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            result = future.result()
            if collector:
                from sentinel.observability.trace import EventType
                collector.emit(
                    EventType.RAG_QUERY_COMPLETED,
                    chunks_returned=len(result.chunks),
                    latency_ms=result.latency_ms,
                )
            return result

    def _build_filter(
        self,
        service: str | None,
        environment: str | None,
    ) -> dict[str, Any] | None:
        """Build Qdrant filter dict from service and environment.

        Uses 'should' semantics for soft boost. Environment filter
        includes 'any' to match runbooks that apply broadly.
        """
        if not service and not environment:
            return None

        filter_dict: dict[str, Any] = {}

        if service:
            filter_dict["service_tags"] = service

        if environment:
            # Match both the specific environment AND 'any'
            filter_dict["environment"] = [environment, "any"]

        return filter_dict

"""Retrieval data models — RunbookChunk, RetrievedChunk, RetrievalResult."""

from typing import Literal

from pydantic import BaseModel, Field


class RunbookChunk(BaseModel):
    """A single chunk of a runbook, enriched with metadata for retrieval."""

    runbook_id: str
    runbook_title: str
    chunk_index: int
    chunk_total: int
    text: str
    service_tags: list[str] = Field(default_factory=list)
    content_sha256: str
    environment: Literal["prod", "staging", "dev", "any"] = "any"


class RetrievedChunk(BaseModel):
    """A chunk returned from vector search, with its relevance score."""

    chunk: RunbookChunk
    score: float  # cosine similarity, post-rerank if reranked
    source: Literal["vector", "reranked"] = "vector"


class RetrievalResult(BaseModel):
    """Complete result of a retrieval query — top-K chunks with metadata."""

    query: str
    chunks: list[RetrievedChunk] = Field(default_factory=list)
    total_tokens: int = 0  # estimated token cost of injected chunks
    latency_ms: int = 0

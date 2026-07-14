"""Fakes shared by triage/engine tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sentinel.retrieval.models import RetrievalResult, RetrievedChunk
from sentinel.triage.extractor import LLMIncidentExtraction

if TYPE_CHECKING:
    from sentinel.observability.trace import TraceCollector


def make_extraction(**overrides: object) -> LLMIncidentExtraction:
    defaults = {
        "title": "Checkout returning 500s",
        "severity": "critical",
        "service": "checkout",
        "environment": "prod",
        "symptom": "Checkout returning HTTP 500 at 90% error rate.",
        "blast_radius": ["us-east-1"],
        "confidence": 0.9,
        "suspected_root_cause": "Recent deploy",
        "suggested_urgency": "immediate",
        "tags": ["database"],
    }
    defaults.update(overrides)
    return LLMIncidentExtraction.model_validate(defaults)


class FakeRetriever:
    """Records the args RunbookRetriever.query() was called with."""

    def __init__(self, chunks: list[RetrievedChunk] | None = None) -> None:
        self.calls: list[dict] = []
        self._chunks = chunks if chunks is not None else []

    def query(
        self,
        text: str,
        service_filter: str | None = None,
        collector: TraceCollector | None = None,
    ) -> RetrievalResult:
        self.calls.append({"text": text, "service_filter": service_filter, "collector": collector})
        return RetrievalResult(query=text, chunks=self._chunks)

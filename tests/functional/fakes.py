"""Fakes for backend endpoint functional tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sentinel.models.incident import IncidentSummary
from sentinel.retrieval.models import RetrievalResult

if TYPE_CHECKING:
    from sentinel.config import Settings
    from sentinel.models.raw_alert import RawAlert
    from sentinel.observability.trace import TraceCollector
    from sentinel.retrieval.retriever import RunbookRetriever


class FakeOwner:
    def __init__(self, service: str = "checkout", team: str = "team-checkout") -> None:
        self.service = service
        self.team = team
        self.tier = 1
        self.manager = "alice"
        self.escalation_channel = "#checkout-oncall"


class FakeDeploy:
    def __init__(self, version: str = "v1.2.3") -> None:
        self.version = version
        self.deployed_at = datetime(2026, 7, 13, 9, 0, 0)
        self.deployed_by = "bob"
        self.change_summary = "Fix checkout bug"


class FakeToolProvider:
    def __init__(
        self,
        owner: FakeOwner | None = None,
        deploys: list[FakeDeploy] | None = None,
        owner_raises: Exception | None = None,
        deploys_raises: Exception | None = None,
    ) -> None:
        self.owner = owner
        self.deploys = deploys if deploys is not None else []
        self.owner_raises = owner_raises
        self.deploys_raises = deploys_raises
        self.owner_calls: list[str] = []
        self.deploy_calls: list[dict] = []

    async def get_service_owner(self, service: str) -> FakeOwner | None:
        self.owner_calls.append(service)
        if self.owner_raises is not None:
            raise self.owner_raises
        return self.owner

    async def get_recent_deploys(
        self, service: str, since: datetime, limit: int = 5
    ) -> list[FakeDeploy]:
        self.deploy_calls.append({"service": service, "since": since, "limit": limit})
        if self.deploys_raises is not None:
            raise self.deploys_raises
        return self.deploys


class FakeGraphRetriever:
    """Retriever whose .query emits RAG collector events (so the graph's
    retrieve node produces rag_query/rag_results) and returns no chunks."""

    def __init__(self, chunks_returned: int = 2) -> None:
        self._chunks_returned = chunks_returned
        self.calls: list[dict] = []

    def query(
        self,
        text: str,
        service_filter: str | None = None,
        collector: TraceCollector | None = None,
    ) -> RetrievalResult:
        self.calls.append({"text": text, "service_filter": service_filter})
        if collector is not None:
            from sentinel.observability.trace import EventType

            collector.emit(EventType.RAG_QUERY_STARTED, query=text[:100])
            collector.emit(
                EventType.RAG_QUERY_COMPLETED,
                chunks_returned=self._chunks_returned,
                latency_ms=12,
            )
        return RetrievalResult(query=text, chunks=[])


def make_fake_triage_alert(
    incident: IncidentSummary, emit_rag_events: bool = False
) -> Callable[..., IncidentSummary]:
    """Build a fake matching sentinel.triage.engine.triage_alert's signature."""

    def _fake_triage_alert(
        alert: RawAlert,
        settings: Settings,
        retriever: RunbookRetriever | None = None,
        enrichment_block: str | None = None,
        collector: TraceCollector | None = None,
    ) -> IncidentSummary:
        if emit_rag_events and collector is not None:
            from sentinel.observability.trace import EventType

            collector.emit(EventType.RAG_QUERY_STARTED, query="checkout 500s")
            collector.emit(
                EventType.RAG_QUERY_COMPLETED, chunks_returned=2, latency_ms=12
            )
        return incident

    return _fake_triage_alert


def make_summary(
    *,
    confidence: float = 0.95,
    proposed_remediation: str | None = "Roll back deploy.",
    **overrides,
) -> IncidentSummary:
    """Canned IncidentSummary for stubbing sentinel.triage.nodes.diagnose_incident.

    Defaults are high-confidence-with-remediation (the auto-approve path); pass
    confidence < 0.80 or proposed_remediation=None to force the human gate.
    """
    base: dict = {
        "incident_id": "11111111-1111-1111-1111-111111111111",
        "title": "Checkout returning 500s",
        "severity": "critical",
        "service": "checkout",
        "environment": "prod",
        "symptom": "Checkout returning HTTP 500 at 90% error rate.",
        "confidence": confidence,
        "suspected_root_cause": "Recent deploy",
        "raw_alert_hash": "a" * 64,
        "timestamp": datetime(2026, 1, 1, tzinfo=UTC),
        "triage_timestamp": datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        "suggested_urgency": "immediate",
        "tags": ["database"],
        "proposed_remediation": proposed_remediation,
    }
    base.update(overrides)
    return IncidentSummary(**base)

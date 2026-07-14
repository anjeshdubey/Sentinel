"""Fakes for backend endpoint functional tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sentinel.config import Settings
    from sentinel.models.incident import IncidentSummary
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
            collector.emit(EventType.RAG_QUERY_COMPLETED, chunks_returned=2, latency_ms=12)
        return incident

    return _fake_triage_alert

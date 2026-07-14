"""Fake protocol implementations shared by tools/provider tests."""

from __future__ import annotations

import asyncio
from datetime import datetime

from sentinel.models.incident import IncidentSummary
from sentinel.tools.models import (
    Deploy,
    EscalationAdvice,
    PastIncident,
    ResolutionRecord,
    ServiceDependencies,
    ServiceOwner,
)


class FakeOwnershipProvider:
    def __init__(self, owner: ServiceOwner | None = None, *, delay: float = 0.0) -> None:
        self.calls = 0
        self._owner = owner
        self._delay = delay

    async def get_service_owner(self, service: str) -> ServiceOwner | None:
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._owner


class FakeDeploysProvider:
    def __init__(self, deploys: list[Deploy] | None = None, *, delay: float = 0.0) -> None:
        self.calls = 0
        self._deploys = deploys if deploys is not None else []
        self._delay = delay

    async def get_recent_deploys(
        self, service: str, since: datetime, limit: int = 5
    ) -> list[Deploy]:
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        return list(self._deploys)


class FakeDependenciesProvider:
    def __init__(self, deps: ServiceDependencies | None = None) -> None:
        self.calls = 0
        self._deps = deps

    async def get_service_dependencies(self, service: str) -> ServiceDependencies | None:
        self.calls += 1
        return self._deps


class FakePastIncidentsProvider:
    def __init__(self, incidents: list[PastIncident] | None = None) -> None:
        self.calls = 0
        self._incidents = incidents if incidents is not None else []

    async def get_similar_past_incidents(
        self, query_text: str, service: str | None = None, k: int = 3
    ) -> list[PastIncident]:
        self.calls += 1
        return list(self._incidents)


class FakeResolutionRecorder:
    def __init__(self) -> None:
        self.calls: list[ResolutionRecord] = []

    async def submit_resolution(self, record: ResolutionRecord) -> None:
        self.calls.append(record)


class FakeEscalationAdvisor:
    def __init__(self, advice: EscalationAdvice) -> None:
        self.calls = 0
        self._advice = advice

    async def get_escalation_advice(self, incident: IncidentSummary) -> EscalationAdvice:
        self.calls += 1
        return self._advice

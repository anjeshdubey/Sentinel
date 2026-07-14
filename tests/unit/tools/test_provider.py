"""Tests for sentinel.tools.provider.ToolProvider and SyncToolProvider."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sentinel.models.incident import IncidentSummary
from sentinel.tools.cache import ToolCache
from sentinel.tools.errors import ToolNotImplemented, ToolTimeout
from sentinel.tools.models import (
    Deploy,
    EscalationAdvice,
    EscalationTarget,
    ServiceDependencies,
    ServiceOwner,
)
from sentinel.tools.protocols import (
    PROTOCOL_VERSION,
    DependenciesProvider,
    DeploysProvider,
    EscalationAdvisor,
    OwnershipProvider,
    PastIncidentsProvider,
    ResolutionRecorder,
)
from sentinel.tools.provider import SyncToolProvider, ToolProvider
from sentinel.tools.stubs import StubEscalationAdvisor, StubResolutionRecorder
from tests.unit.tools.fakes import (
    FakeDependenciesProvider,
    FakeDeploysProvider,
    FakeEscalationAdvisor,
    FakeOwnershipProvider,
    FakePastIncidentsProvider,
    FakeResolutionRecorder,
)

DEFAULT_TIMEOUTS = {
    "ownership": 1000,
    "deploys": 1000,
    "dependencies": 1000,
    "past_incidents": 1000,
    "submit_resolution": 1000,
    "get_escalation_advice": 1000,
}


def _build_provider(
    *,
    ownership: OwnershipProvider | None = None,
    deploys: DeploysProvider | None = None,
    dependencies: DependenciesProvider | None = None,
    past_incidents: PastIncidentsProvider | None = None,
    resolution: ResolutionRecorder | None = None,
    escalation: EscalationAdvisor | None = None,
    timeouts_ms: dict | None = None,
    cache: ToolCache | None = None,
    backend_protocol_version: tuple[int, int] = PROTOCOL_VERSION,
) -> ToolProvider:
    return ToolProvider(
        ownership=ownership or FakeOwnershipProvider(),
        deploys=deploys or FakeDeploysProvider(),
        dependencies=dependencies or FakeDependenciesProvider(),
        past_incidents=past_incidents or FakePastIncidentsProvider(),
        resolution=resolution or StubResolutionRecorder(),
        escalation=escalation or StubEscalationAdvisor(),
        timeouts_ms=timeouts_ms or DEFAULT_TIMEOUTS,
        cache=cache,
        backend_protocol_version=backend_protocol_version,
    )


class TestProtocolVersionCheck:
    def test_major_version_mismatch_raises(self) -> None:
        with pytest.raises(RuntimeError, match="major mismatch"):
            _build_provider(backend_protocol_version=(PROTOCOL_VERSION[0] + 1, 0))

    def test_minor_version_mismatch_allowed(self) -> None:
        provider = _build_provider(
            backend_protocol_version=(PROTOCOL_VERSION[0], PROTOCOL_VERSION[1] + 5)
        )

        assert isinstance(provider, ToolProvider)


class TestTimeout:
    async def test_slow_backend_raises_tool_timeout(self) -> None:
        provider = _build_provider(
            ownership=FakeOwnershipProvider(delay=0.05),
            timeouts_ms={**DEFAULT_TIMEOUTS, "ownership": 10},
        )

        with pytest.raises(ToolTimeout) as exc_info:
            await provider.get_service_owner("checkout")

        assert exc_info.value.tool == "ownership"
        assert exc_info.value.timeout_ms == 10

    async def test_fast_backend_within_timeout_succeeds(self) -> None:
        owner = ServiceOwner(service="checkout", team="team-a", tier=1, escalation_channel="#a")
        provider = _build_provider(
            ownership=FakeOwnershipProvider(owner, delay=0.01),
            timeouts_ms={**DEFAULT_TIMEOUTS, "ownership": 500},
        )

        result = await provider.get_service_owner("checkout")

        assert result == owner


class TestListFreezing:
    async def test_deploy_list_result_is_frozen_to_tuple(self) -> None:
        deploy = Deploy(
            service="checkout",
            version="v1",
            deployed_at=datetime(2026, 1, 1, tzinfo=UTC),
            deployed_by="alice",
        )
        provider = _build_provider(deploys=FakeDeploysProvider([deploy]))

        result = await provider.get_recent_deploys(
            "checkout", since=datetime(2026, 1, 1, tzinfo=UTC)
        )

        assert isinstance(result, tuple)
        assert result == (deploy,)

    async def test_past_incidents_list_result_is_frozen_to_tuple(self) -> None:
        provider = _build_provider(past_incidents=FakePastIncidentsProvider([]))

        result = await provider.get_similar_past_incidents("checkout down")

        assert isinstance(result, tuple)


class TestCacheDelegation:
    async def test_repeated_calls_hit_cache_when_configured(self) -> None:
        fake = FakeOwnershipProvider(
            ServiceOwner(service="checkout", team="team-a", tier=1, escalation_channel="#a")
        )
        cache = ToolCache(ttl_by_tool={"ownership": 100})
        provider = _build_provider(ownership=fake, cache=cache)

        await provider.get_service_owner("checkout")
        await provider.get_service_owner("checkout")

        assert fake.calls == 1

    async def test_repeated_calls_hit_backend_every_time_without_cache(self) -> None:
        fake = FakeOwnershipProvider(
            ServiceOwner(service="checkout", team="team-a", tier=1, escalation_channel="#a")
        )
        provider = _build_provider(ownership=fake, cache=None)

        await provider.get_service_owner("checkout")
        await provider.get_service_owner("checkout")

        assert fake.calls == 2


class TestMethodRouting:
    async def test_get_service_owner_routes_to_ownership_backend(self) -> None:
        owner = ServiceOwner(service="checkout", team="team-a", tier=1, escalation_channel="#a")
        fake = FakeOwnershipProvider(owner)
        provider = _build_provider(ownership=fake)

        result = await provider.get_service_owner("checkout")

        assert result == owner
        assert fake.calls == 1

    async def test_get_recent_deploys_routes_to_deploys_backend(self) -> None:
        fake = FakeDeploysProvider([])
        provider = _build_provider(deploys=fake)

        await provider.get_recent_deploys(
            "checkout", since=datetime(2026, 1, 1, tzinfo=UTC), limit=3
        )

        assert fake.calls == 1

    async def test_get_service_dependencies_routes_to_dependencies_backend(self) -> None:
        deps = ServiceDependencies(service="checkout", upstream=["auth"], downstream=[])
        fake = FakeDependenciesProvider(deps)
        provider = _build_provider(dependencies=fake)

        result = await provider.get_service_dependencies("checkout")

        assert result == deps
        assert fake.calls == 1

    async def test_get_similar_past_incidents_routes_to_past_incidents_backend(self) -> None:
        fake = FakePastIncidentsProvider([])
        provider = _build_provider(past_incidents=fake)

        await provider.get_similar_past_incidents("checkout down", service="checkout", k=5)

        assert fake.calls == 1

    async def test_submit_resolution_routes_to_resolution_backend(self) -> None:
        fake = FakeResolutionRecorder()
        provider = _build_provider(resolution=fake)
        from sentinel.tools.models import ResolutionRecord

        record = ResolutionRecord(
            incident_id="abc",
            resolution_summary="fixed it",
            resolved_at=datetime(2026, 1, 1, tzinfo=UTC),
            resolved_by="alice",
        )

        await provider.submit_resolution(record)

        assert fake.calls == [record]

    async def test_get_escalation_advice_routes_to_escalation_backend(
        self, valid_incident_kwargs: dict
    ) -> None:
        advice = EscalationAdvice(
            target=EscalationTarget(type="slack", value="#checkout-oncall"),
            urgency="immediate",
            tier=1,
            reason="critical outage",
        )
        fake = FakeEscalationAdvisor(advice)
        provider = _build_provider(escalation=fake)
        incident = IncidentSummary.model_validate(valid_incident_kwargs)

        result = await provider.get_escalation_advice(incident)

        assert result == advice
        assert fake.calls == 1

    async def test_stub_resolution_recorder_raises_not_implemented(self) -> None:
        provider = _build_provider(resolution=StubResolutionRecorder())
        from sentinel.tools.models import ResolutionRecord

        record = ResolutionRecord(
            incident_id="abc",
            resolution_summary="fixed it",
            resolved_at=datetime(2026, 1, 1, tzinfo=UTC),
            resolved_by="alice",
        )

        with pytest.raises(ToolNotImplemented):
            await provider.submit_resolution(record)

    async def test_stub_escalation_advisor_raises_not_implemented(
        self, valid_incident_kwargs: dict
    ) -> None:
        provider = _build_provider(escalation=StubEscalationAdvisor())
        incident = IncidentSummary.model_validate(valid_incident_kwargs)

        with pytest.raises(ToolNotImplemented):
            await provider.get_escalation_advice(incident)


class TestSyncToolProvider:
    def test_sync_facade_wraps_async_calls(self) -> None:
        owner = ServiceOwner(service="checkout", team="team-a", tier=1, escalation_channel="#a")
        provider = _build_provider(ownership=FakeOwnershipProvider(owner))
        sync_provider = SyncToolProvider(provider)

        result = sync_provider.get_service_owner("checkout")

        assert result == owner

    def test_sync_facade_get_recent_deploys(self) -> None:
        deploy = Deploy(
            service="checkout",
            version="v1",
            deployed_at=datetime(2026, 1, 1, tzinfo=UTC),
            deployed_by="alice",
        )
        provider = _build_provider(deploys=FakeDeploysProvider([deploy]))
        sync_provider = SyncToolProvider(provider)

        result = sync_provider.get_recent_deploys(
            "checkout", since=datetime(2026, 1, 1, tzinfo=UTC)
        )

        assert result == (deploy,)

    def test_sync_facade_get_service_dependencies(self) -> None:
        deps = ServiceDependencies(service="checkout", upstream=["auth"], downstream=[])
        provider = _build_provider(dependencies=FakeDependenciesProvider(deps))
        sync_provider = SyncToolProvider(provider)

        assert sync_provider.get_service_dependencies("checkout") == deps

    def test_sync_facade_get_similar_past_incidents(self) -> None:
        provider = _build_provider(past_incidents=FakePastIncidentsProvider([]))
        sync_provider = SyncToolProvider(provider)

        assert sync_provider.get_similar_past_incidents("checkout down") == ()

    def test_sync_facade_submit_resolution(self) -> None:
        from sentinel.tools.models import ResolutionRecord

        fake = FakeResolutionRecorder()
        provider = _build_provider(resolution=fake)
        sync_provider = SyncToolProvider(provider)
        record = ResolutionRecord(
            incident_id="abc",
            resolution_summary="fixed it",
            resolved_at=datetime(2026, 1, 1, tzinfo=UTC),
            resolved_by="alice",
        )

        sync_provider.submit_resolution(record)

        assert fake.calls == [record]

    def test_sync_facade_get_escalation_advice(self, valid_incident_kwargs: dict) -> None:
        advice = EscalationAdvice(
            target=EscalationTarget(type="slack", value="#checkout-oncall"),
            urgency="immediate",
            tier=1,
            reason="critical outage",
        )
        provider = _build_provider(escalation=FakeEscalationAdvisor(advice))
        sync_provider = SyncToolProvider(provider)
        incident = IncidentSummary.model_validate(valid_incident_kwargs)

        assert sync_provider.get_escalation_advice(incident) == advice

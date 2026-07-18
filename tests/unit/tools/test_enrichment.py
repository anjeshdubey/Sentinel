"""Unit tests for sentinel.tools.enrichment.gather_context.

Covers the extracted CMDB orchestration (Week 5 PR 2): owner/deploy resolution,
fail-open on ToolError, block rendering, the deploy-window arithmetic, and the
byte-shape of the optional on_event frames the SSE demo relies on.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sentinel.models.raw_alert import RawAlert
from sentinel.tools.enrichment import EnrichmentResult, gather_context
from sentinel.tools.errors import ToolBackendError
from sentinel.tools.models import Deploy, ServiceOwner

ALERT_TS = datetime(2026, 7, 13, 10, 0, 0)


def make_alert(payload: dict | None = None, metadata: dict | None = None) -> RawAlert:
    return RawAlert(
        source="pagerduty",
        timestamp=ALERT_TS,
        raw_payload=payload if payload is not None else {"service": "checkout"},
        metadata=metadata or {},
    )


def make_owner(service: str = "checkout-api") -> ServiceOwner:
    return ServiceOwner(
        service=service,
        team="team-checkout",
        tier=1,
        escalation_channel="#checkout-oncall",
        manager="alice",
        aliases=["checkout"],
    )


def make_deploy(version: str = "v1.2.3") -> Deploy:
    return Deploy(
        service="checkout-api",
        version=version,
        deployed_at=datetime(2026, 7, 13, 9, 0, 0),
        deployed_by="bob",
        change_summary="Fix checkout bug",
    )


class FakeProvider:
    """Combined ownership+deploys fake that records calls and can fail open."""

    def __init__(
        self,
        owner: ServiceOwner | None = None,
        deploys: list[Deploy] | None = None,
        owner_raises: Exception | None = None,
        deploys_raises: Exception | None = None,
    ) -> None:
        self._owner = owner
        self._deploys = list(deploys) if deploys is not None else []
        self._owner_raises = owner_raises
        self._deploys_raises = deploys_raises
        self.owner_calls: list[str] = []
        self.deploy_calls: list[dict] = []

    async def get_service_owner(self, service: str) -> ServiceOwner | None:
        self.owner_calls.append(service)
        if self._owner_raises is not None:
            raise self._owner_raises
        return self._owner

    async def get_recent_deploys(
        self, service: str, since: datetime, limit: int = 5
    ) -> list[Deploy]:
        self.deploy_calls.append({"service": service, "since": since, "limit": limit})
        if self._deploys_raises is not None:
            raise self._deploys_raises
        return list(self._deploys)


def _record_events() -> tuple[list[tuple[str, dict]], object]:
    events: list[tuple[str, dict]] = []

    def on_event(event: str, data: dict) -> None:
        events.append((event, data))

    return events, on_event


class TestHappyPath:
    async def test_owner_and_deploys_resolved(self) -> None:
        provider = FakeProvider(owner=make_owner(), deploys=[make_deploy()])

        result = await gather_context(make_alert(), provider)

        assert isinstance(result, EnrichmentResult)
        assert result.service_hint == "checkout"
        # Owner hit resolves the canonical service used for the deploy lookup.
        assert result.resolved_service == "checkout-api"
        assert result.owner is provider._owner
        assert result.owner_team == "team-checkout"
        assert [d.version for d in result.deploys] == ["v1.2.3"]

    async def test_deploy_lookup_uses_resolved_service(self) -> None:
        provider = FakeProvider(owner=make_owner(), deploys=[make_deploy()])

        await gather_context(make_alert(), provider)

        assert provider.owner_calls == ["checkout"]
        assert provider.deploy_calls[0]["service"] == "checkout-api"

    async def test_enrichment_block_rendering(self) -> None:
        provider = FakeProvider(owner=make_owner(), deploys=[make_deploy()])

        result = await gather_context(make_alert(), provider)

        assert result.enrichment_block == (
            "Service owner: team-checkout (tier 1)\n"
            "Recent deploys:\n"
            "- v1.2.3 at 2026-07-13T09:00:00: Fix checkout bug"
        )


class TestServiceResolution:
    async def test_no_service_hint_skips_all_tool_calls(self) -> None:
        provider = FakeProvider(owner=make_owner(), deploys=[make_deploy()])
        alert = make_alert(payload={"note": "the widget is broken please help"})
        events, on_event = _record_events()

        result = await gather_context(alert, provider, on_event=on_event)

        assert provider.owner_calls == []
        assert provider.deploy_calls == []
        assert events == []
        assert result.service_hint is None
        assert result.resolved_service is None
        assert result.owner is None
        assert result.deploys == ()
        assert result.enrichment_block is None

    async def test_owner_miss_still_runs_deploys_with_hint(self) -> None:
        provider = FakeProvider(owner=None, deploys=[make_deploy()])

        result = await gather_context(make_alert(), provider)

        # No owner -> resolved service falls back to the raw hint.
        assert result.resolved_service == "checkout"
        assert provider.deploy_calls[0]["service"] == "checkout"
        assert result.owner is None
        assert result.owner_team is None
        assert [d.version for d in result.deploys] == ["v1.2.3"]
        # Block still rendered from the deploys alone.
        assert result.enrichment_block == (
            "Recent deploys:\n- v1.2.3 at 2026-07-13T09:00:00: Fix checkout bug"
        )

    async def test_owner_only_block(self) -> None:
        provider = FakeProvider(owner=make_owner(), deploys=[])

        result = await gather_context(make_alert(), provider)

        assert result.enrichment_block == "Service owner: team-checkout (tier 1)"

    async def test_no_owner_no_deploys_yields_no_block(self) -> None:
        provider = FakeProvider(owner=None, deploys=[])

        result = await gather_context(make_alert(), provider)

        assert result.enrichment_block is None
        assert result.owner_team is None


class TestFailOpen:
    async def test_owner_tool_error_fails_open_and_continues(self) -> None:
        provider = FakeProvider(
            owner_raises=ToolBackendError("ownership", "boom"), deploys=[make_deploy()]
        )
        events, on_event = _record_events()

        result = await gather_context(make_alert(), provider, on_event=on_event)

        assert result.owner is None
        assert result.owner_team is None
        # Deploy lookup still runs against the raw hint.
        assert [d.version for d in result.deploys] == ["v1.2.3"]
        owner_result = next(
            d["result"]
            for e, d in events
            if e == "tool_result" and d["tool"] == "get_service_owner"
        )
        assert owner_result is None

    async def test_deploys_tool_error_fails_open(self) -> None:
        provider = FakeProvider(
            owner=make_owner(), deploys_raises=ToolBackendError("deploys", "boom")
        )
        events, on_event = _record_events()

        result = await gather_context(make_alert(), provider, on_event=on_event)

        assert result.deploys == ()
        # Owner-only block still renders.
        assert result.enrichment_block == "Service owner: team-checkout (tier 1)"
        deploys_result = next(
            d["result"]
            for e, d in events
            if e == "tool_result" and d["tool"] == "get_recent_deploys"
        )
        assert deploys_result == []


class TestDeployWindow:
    async def test_default_window_is_two_hours(self) -> None:
        provider = FakeProvider(owner=make_owner(), deploys=[])

        await gather_context(make_alert(), provider)

        assert provider.deploy_calls[0]["since"] == ALERT_TS - timedelta(hours=2)

    async def test_since_hours_controls_window_and_event_arg(self) -> None:
        provider = FakeProvider(owner=make_owner(), deploys=[])
        events, on_event = _record_events()

        await gather_context(make_alert(), provider, since_hours=6, on_event=on_event)

        assert provider.deploy_calls[0]["since"] == ALERT_TS - timedelta(hours=6)
        call_args = next(
            d["args"]
            for e, d in events
            if e == "tool_call" and d["tool"] == "get_recent_deploys"
        )
        assert call_args == {"service": "checkout-api", "hours": 6}


class TestEventEmission:
    async def test_emits_byte_identical_frame_shapes(self) -> None:
        provider = FakeProvider(owner=make_owner(), deploys=[make_deploy()])
        events, on_event = _record_events()

        await gather_context(make_alert(), provider, on_event=on_event)

        names = [e for e, _ in events]
        assert names == ["tool_call", "tool_result", "tool_call", "tool_result"]

        assert events[0] == (
            "tool_call",
            {"tool": "get_service_owner", "args": {"service": "checkout"}},
        )

        _, owner_res = events[1]
        assert owner_res["tool"] == "get_service_owner"
        assert owner_res["result"] == {
            "owner_team": "team-checkout",
            "tier": 1,
            "oncall": "alice",
            "escalation_channel": "#checkout-oncall",
        }
        assert isinstance(owner_res["duration_ms"], int)

        assert events[2] == (
            "tool_call",
            {
                "tool": "get_recent_deploys",
                "args": {"service": "checkout-api", "hours": 2},
            },
        )

        _, deploy_res = events[3]
        assert deploy_res["tool"] == "get_recent_deploys"
        assert deploy_res["result"] == [
            {
                "version": "v1.2.3",
                "deployed_at": "2026-07-13T09:00:00",
                "deployer": "bob",
                "commit_message": "Fix checkout bug",
            }
        ]
        assert isinstance(deploy_res["duration_ms"], int)

    async def test_runs_tools_without_on_event(self) -> None:
        provider = FakeProvider(owner=make_owner(), deploys=[make_deploy()])

        result = await gather_context(make_alert(), provider)

        # Tools still execute and populate the result even with no callback.
        assert provider.owner_calls == ["checkout"]
        assert len(provider.deploy_calls) == 1
        assert result.enrichment_block is not None

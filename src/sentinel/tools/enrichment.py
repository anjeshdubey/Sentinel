"""Alert enrichment — CMDB orchestration lifted out of the demo endpoint.

Runs the ownership → deploys tool sequence for an alert and renders the
compact enrichment block that gets injected into the triage prompt. This was
inlined in ``backend/demo_endpoints.py``; it lives here so the demo endpoint
and the LangGraph ``enrich`` node (Week 5 PR 3) share one implementation
rather than drifting.

Behavior is preserved from the original endpoint code:
  - ``get_service_owner`` runs first; a hit resolves the canonical service
    name used for the deploy lookup.
  - ``get_recent_deploys`` runs over the ``since_hours`` window before the
    alert timestamp.
  - Any ``ToolError`` fails open (owner -> None, deploys -> empty) so
    enrichment is best-effort, never a hard dependency.
  - The rendered block matches the original line-by-line format exactly.

Callers that want to surface progress (the SSE demo) pass ``on_event``; it is
invoked with the same ``tool_call`` / ``tool_result`` payloads the endpoint
used to emit inline, so the event stream stays byte-identical. Headless
callers (graph nodes, tests) omit it and just read the returned result.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from sentinel.models.raw_alert import RawAlert
from sentinel.retrieval.query_builder import _guess_service
from sentinel.tools.errors import ToolError
from sentinel.tools.models import Deploy, ServiceOwner

# Callback signature for surfacing enrichment progress as (event, data) pairs.
EventEmitter = Callable[[str, dict], None]


class EnrichmentToolProvider(Protocol):
    """The subset of the tool provider that enrichment depends on.

    ``ToolProvider`` satisfies this structurally; tests can pass any object
    exposing these two coroutines.
    """

    async def get_service_owner(self, service: str) -> ServiceOwner | None: ...

    async def get_recent_deploys(
        self, service: str, since: datetime, limit: int = 5
    ) -> list[Deploy]: ...


@dataclass
class EnrichmentResult:
    """Aggregated CMDB context for an alert.

    A plain container (not a Pydantic model) so it can hold whatever
    duck-typed owner/deploy objects a backend or fake returns without
    triggering validation.

    Attributes:
        service_hint: Service guessed from the alert, before owner resolution.
        resolved_service: Canonical service name used for the deploy lookup
            (the owner's canonical name on a hit, else the hint).
        owner: ServiceOwner on a hit, None on miss or tool error.
        deploys: Recent deploys in the window (empty on miss or tool error).
        enrichment_block: Rendered markdown for prompt injection, or None when
            there is nothing to enrich with.
    """

    service_hint: str | None = None
    resolved_service: str | None = None
    owner: ServiceOwner | None = None
    deploys: tuple[Deploy, ...] = ()
    enrichment_block: str | None = None

    @property
    def owner_team(self) -> str | None:
        """Owning team name, or None when the owner is unknown."""
        return self.owner.team if self.owner else None


def _owner_payload(owner: ServiceOwner | None) -> dict | None:
    if owner is None:
        return None
    return {
        "owner_team": owner.team,
        "tier": owner.tier,
        "oncall": owner.manager,
        "escalation_channel": owner.escalation_channel,
    }


def _deploys_payload(deploys: tuple[Deploy, ...]) -> list[dict]:
    return [
        {
            "version": d.version,
            "deployed_at": d.deployed_at.isoformat(),
            "deployer": d.deployed_by,
            "commit_message": d.change_summary,
        }
        for d in deploys
    ]


def _render_block(
    owner: ServiceOwner | None, deploys: tuple[Deploy, ...]
) -> str | None:
    if not (owner or deploys):
        return None
    lines: list[str] = []
    if owner:
        lines.append(f"Service owner: {owner.team} (tier {owner.tier})")
    if deploys:
        lines.append("Recent deploys:")
        for d in deploys:
            lines.append(
                f"- {d.version} at {d.deployed_at.isoformat()}: {d.change_summary}"
            )
    return "\n".join(lines)


async def gather_context(
    alert: RawAlert,
    tool_provider: EnrichmentToolProvider,
    *,
    since_hours: int = 2,
    on_event: EventEmitter | None = None,
) -> EnrichmentResult:
    """Run ownership + deploy enrichment for an alert.

    Args:
        alert: The alert being triaged. Its payload/metadata seed the service
            guess; its timestamp anchors the deploy window.
        tool_provider: Provides ``get_service_owner`` / ``get_recent_deploys``.
        since_hours: Deploy lookback window, in hours before the alert.
        on_event: Optional callback receiving ``(event, data)`` pairs for each
            tool call/result — matches the demo SSE frame shape. Omit for
            headless use.

    Returns:
        EnrichmentResult with the resolved service, owner, deploys, and
        rendered enrichment block. Fails open on ToolError.
    """

    def emit(event: str, data: dict) -> None:
        if on_event is not None:
            on_event(event, data)

    service_hint = _guess_service(alert.raw_payload, alert.metadata)
    result = EnrichmentResult(service_hint=service_hint, resolved_service=service_hint)

    # No service to look up -> nothing to enrich, no tool calls emitted.
    if not service_hint:
        return result

    # --- get_service_owner ---
    t0 = time.perf_counter()
    emit("tool_call", {"tool": "get_service_owner", "args": {"service": service_hint}})
    try:
        owner = await tool_provider.get_service_owner(service_hint)
    except ToolError:
        owner = None
    duration_ms = int((time.perf_counter() - t0) * 1000)
    emit(
        "tool_result",
        {
            "tool": "get_service_owner",
            "result": _owner_payload(owner),
            "duration_ms": duration_ms,
        },
    )
    result.owner = owner
    if owner:
        result.resolved_service = owner.service

    # --- get_recent_deploys ---
    t0 = time.perf_counter()
    emit(
        "tool_call",
        {
            "tool": "get_recent_deploys",
            "args": {"service": result.resolved_service, "hours": since_hours},
        },
    )
    try:
        deploys: tuple[Deploy, ...] = tuple(
            await tool_provider.get_recent_deploys(
                result.resolved_service,
                since=alert.timestamp - timedelta(hours=since_hours),
            )
        )
    except ToolError:
        deploys = ()
    duration_ms = int((time.perf_counter() - t0) * 1000)
    emit(
        "tool_result",
        {
            "tool": "get_recent_deploys",
            "result": _deploys_payload(deploys),
            "duration_ms": duration_ms,
        },
    )
    result.deploys = deploys

    result.enrichment_block = _render_block(owner, deploys)
    return result

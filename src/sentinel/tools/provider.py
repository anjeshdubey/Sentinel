"""ToolProvider container + SyncToolProvider facade.

ToolProvider bundles all six protocol implementations with cross-cutting
concerns: per-tool timeouts, caching with single-flight dedup, and
immutability enforcement for list returns.

SyncToolProvider wraps each method in asyncio.run() for CLI usage where
no event loop is running. LangGraph, FastAPI, and M5 workers call
ToolProvider directly (async).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Awaitable, Callable

from sentinel.tools.cache import ToolCache
from sentinel.tools.errors import ToolTimeout
from sentinel.tools.models import (
    Deploy,
    EscalationAdvice,
    PastIncident,
    ResolutionRecord,
    ServiceDependencies,
    ServiceOwner,
)
from sentinel.tools.protocols import (
    PROTOCOL_VERSION,
    DeploysProvider,
    DependenciesProvider,
    EscalationAdvisor,
    OwnershipProvider,
    PastIncidentsProvider,
    ResolutionRecorder,
)

from sentinel.models.incident import IncidentSummary


class ToolProvider:
    """Async container bundling all six tool interfaces + cross-cutting concerns.

    Cross-cutting behavior applied by _call():
    - Per-tool timeout via asyncio.wait_for
    - Cache lookup / single-flight dedup via ToolCache
    - Immutability enforcement: list[T] results frozen to tuple before returning

    Args:
        ownership: OwnershipProvider implementation.
        deploys: DeploysProvider implementation.
        dependencies: DependenciesProvider implementation.
        past_incidents: PastIncidentsProvider implementation.
        resolution: ResolutionRecorder implementation (M5 stub for M2).
        escalation: EscalationAdvisor implementation (M5 stub for M2).
        timeouts_ms: Per-tool timeout mapping (tool_name -> milliseconds).
        cache: Optional ToolCache for TTL + single-flight.
        backend_protocol_version: Protocol version the backends were built against.
    """

    def __init__(
        self,
        ownership: OwnershipProvider,
        deploys: DeploysProvider,
        dependencies: DependenciesProvider,
        past_incidents: PastIncidentsProvider,
        resolution: ResolutionRecorder,
        escalation: EscalationAdvisor,
        timeouts_ms: dict[str, int],
        cache: ToolCache | None = None,
        backend_protocol_version: tuple[int, int] = PROTOCOL_VERSION,
    ) -> None:
        # Fail loudly at composition root if a backend was built against
        # an older/newer major protocol version.
        if backend_protocol_version[0] != PROTOCOL_VERSION[0]:
            raise RuntimeError(
                f"backend built for protocol {backend_protocol_version} "
                f"but engine expects {PROTOCOL_VERSION} (major mismatch)"
            )
        self._ownership = ownership
        self._deploys = deploys
        self._dependencies = dependencies
        self._past_incidents = past_incidents
        self._resolution = resolution
        self._escalation = escalation
        self._timeouts_ms = timeouts_ms
        self._cache = cache

    async def get_service_owner(self, service: str) -> ServiceOwner | None:
        """Look up ownership for a service (with alias resolution)."""
        return await self._call(
            "ownership",
            key=("ownership", service),
            fn=lambda: self._ownership.get_service_owner(service),
        )

    async def get_recent_deploys(
        self, service: str, since: datetime, limit: int = 5
    ) -> tuple[Deploy, ...]:
        """Get recent deploys for a service, newest-first."""
        return await self._call(
            "deploys",
            key=("deploys", service, since.isoformat(), limit),
            fn=lambda: self._deploys.get_recent_deploys(service, since, limit),
        )

    async def get_service_dependencies(
        self, service: str
    ) -> ServiceDependencies | None:
        """Get upstream/downstream dependencies for a service."""
        return await self._call(
            "dependencies",
            key=("dependencies", service),
            fn=lambda: self._dependencies.get_service_dependencies(service),
        )

    async def get_similar_past_incidents(
        self, query_text: str, service: str | None = None, k: int = 3
    ) -> tuple[PastIncident, ...]:
        """Find semantically similar past incidents."""
        return await self._call(
            "past_incidents",
            key=("past_incidents", query_text, service, k),
            fn=lambda: self._past_incidents.get_similar_past_incidents(
                query_text, service, k
            ),
        )

    async def submit_resolution(self, record: ResolutionRecord) -> None:
        """Submit a resolution record (Month 5 — raises ToolNotImplemented)."""
        return await self._call(
            "submit_resolution",
            key=("submit_resolution", record.incident_id),
            fn=lambda: self._resolution.submit_resolution(record),
        )

    async def get_escalation_advice(
        self, incident: IncidentSummary
    ) -> EscalationAdvice:
        """Get escalation advice (Month 5 — raises ToolNotImplemented)."""
        return await self._call(
            "get_escalation_advice",
            key=("get_escalation_advice", incident.incident_id),
            fn=lambda: self._escalation.get_escalation_advice(incident),
        )

    async def _call(
        self,
        tool_name: str,
        key: tuple[Any, ...],
        fn: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Execute a tool call with timeout, caching, and immutability.

        Steps:
        1. If cache is configured, delegate to cache.get_or_call (handles
           single-flight dedup and TTL).
        2. Otherwise, call the backend directly with timeout.
        3. Freeze list[T] results to tuple for immutability.
        """
        timeout_ms = self._timeouts_ms.get(tool_name, 500)

        async def _do() -> Any:
            try:
                result = await asyncio.wait_for(
                    fn(), timeout=timeout_ms / 1000.0
                )
            except asyncio.TimeoutError:
                raise ToolTimeout(tool_name, timeout_ms)
            # Freeze list results to prevent cache poisoning via mutation.
            # Single-flight cache returns the same reference to all waiters.
            if isinstance(result, list):
                return tuple(result)
            return result

        if self._cache:
            return await self._cache.get_or_call(key, _do, tool=tool_name)
        return await _do()


class SyncToolProvider:
    """Thin synchronous facade for the CLI and simple tests.

    Wraps every call in asyncio.run() — safe because the CLI has no event loop.
    LangGraph and FastAPI callers use ToolProvider directly (async).

    WARNING: Do not use from inside a running event loop (Jupyter, some IDEs).
    Use the async ToolProvider directly in those contexts.
    """

    def __init__(self, inner: ToolProvider) -> None:
        self._inner = inner

    def get_service_owner(self, service: str) -> ServiceOwner | None:
        """Look up ownership for a service."""
        return asyncio.run(self._inner.get_service_owner(service))

    def get_recent_deploys(
        self, service: str, since: datetime, limit: int = 5
    ) -> tuple[Deploy, ...]:
        """Get recent deploys for a service."""
        return asyncio.run(self._inner.get_recent_deploys(service, since, limit))

    def get_service_dependencies(
        self, service: str
    ) -> ServiceDependencies | None:
        """Get dependencies for a service."""
        return asyncio.run(self._inner.get_service_dependencies(service))

    def get_similar_past_incidents(
        self, query_text: str, service: str | None = None, k: int = 3
    ) -> tuple[PastIncident, ...]:
        """Find similar past incidents."""
        return asyncio.run(
            self._inner.get_similar_past_incidents(query_text, service, k)
        )

    def submit_resolution(self, record: ResolutionRecord) -> None:
        """Submit a resolution (Month 5 stub)."""
        return asyncio.run(self._inner.submit_resolution(record))

    def get_escalation_advice(
        self, incident: IncidentSummary
    ) -> EscalationAdvice:
        """Get escalation advice (Month 5 stub)."""
        return asyncio.run(self._inner.get_escalation_advice(incident))

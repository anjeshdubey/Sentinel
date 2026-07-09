"""LOCKED after Spike 2 architect review.

Any change to a Protocol signature in this module requires:
1. Bumping PROTOCOL_VERSION.
2. Recording rationale in docs/planning/protocol_lock_log.md.
3. An architect sign-off in the review log.

Rationale: two backends (JSON now, real in M5), one engine, LangGraph
nodes in M3, and three parallel workers in M5 all bind to these
signatures. Silent drift cascades.

Version semantics:
    - Minor bump: additive changes only (new optional kwarg with default,
      new optional field on return type, new tool protocol alongside existing).
      Old backends continue to work.
    - Major bump: anything else (rename, type change, removed default).
      Old backends fail the ToolProvider.__init__ version check.

Return-vs-raise contract (applies to ALL protocols):
    - None = "the thing you asked for doesn't exist" (single-item returns)
    - Empty list = "nothing matched" (collection returns)
    - Raise ToolTimeout = "backend exceeded per-tool timeout"
    - Raise ToolBackendError = "backend broke (transport, parse, auth, 5xx)"
    - list[T] values are contractually read-only. ToolProvider enforces this
      by wrapping every list result as tuple() before it reaches callers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from sentinel.models.incident import IncidentSummary
from sentinel.tools.models import (
    Deploy,
    EscalationAdvice,
    PastIncident,
    ResolutionRecord,
    ServiceDependencies,
    ServiceOwner,
)

# Bump on any signature change to any Protocol below.
# Major = breaking; minor = additive-only.
PROTOCOL_VERSION: tuple[int, int] = (1, 0)


@runtime_checkable
class OwnershipProvider(Protocol):
    """Provides service ownership information.

    Contract:
      - Returns ServiceOwner on hit, None on miss (service unknown).
      - Raises ToolTimeout on timeout, ToolBackendError on transport/parse errors.
      - Never returns a partial or malformed ServiceOwner.
      - Idempotent + cache-friendly (owner data is stable minute-to-minute).
    """

    async def get_service_owner(self, service: str) -> ServiceOwner | None: ...


@runtime_checkable
class DeploysProvider(Protocol):
    """Provides recent deploy history for a service.

    Contract:
      - Returns list ordered newest-first. Empty list on 'no deploys in
        window' — NOT None (there is no ambiguity to signal).
      - Raises ToolTimeout / ToolBackendError on backend failures.
      - Cache TTL is short (60s) — deploys are freshness-sensitive.
      - list[Deploy] is contractually read-only.
    """

    async def get_recent_deploys(
        self, service: str, since: datetime, limit: int = 5
    ) -> list[Deploy]: ...


@runtime_checkable
class DependenciesProvider(Protocol):
    """Provides upstream/downstream service dependency graph.

    Contract:
      - Returns ServiceDependencies on hit, None on miss.
      - Raises ToolTimeout / ToolBackendError on backend failures.
    """

    async def get_service_dependencies(
        self, service: str
    ) -> ServiceDependencies | None: ...


@runtime_checkable
class PastIncidentsProvider(Protocol):
    """Provides semantically similar past incidents.

    Contract:
      - Returns list ordered by rank (1..k), never longer than k.
      - Empty list on 'no similar incidents' — NOT None.
      - Every returned PastIncident has rank populated and relevance bucketed.
      - Cosine scores are NOT part of the return type.
      - Raises ToolTimeout / ToolBackendError on backend failures.
      - list[PastIncident] is contractually read-only.
    """

    async def get_similar_past_incidents(
        self, query_text: str, service: str | None = None, k: int = 3
    ) -> list[PastIncident]: ...


# --- Month 5 stubs, protocol locked now ---


@runtime_checkable
class ResolutionRecorder(Protocol):
    """Records how an incident was resolved (Month 5).

    Stubbed for M2 (raises ToolNotImplemented).
    Contract:
      - Returns nothing on success.
      - Raises ToolNotFound if incident_id does not exist.
      - Raises ToolBackendError on transport error.
      - Idempotent on identical payloads (safe retry).
    """

    async def submit_resolution(self, record: ResolutionRecord) -> None: ...


@runtime_checkable
class EscalationAdvisor(Protocol):
    """Provides escalation routing advice (Month 5).

    Stubbed for M2 (raises ToolNotImplemented).
    Contract:
      - Returns EscalationAdvice on success (never None — every incident
        has *some* advice, even if it is 'monitor / no team').
      - Raises ToolBackendError on transport error.
    """

    async def get_escalation_advice(
        self, incident: IncidentSummary
    ) -> EscalationAdvice: ...

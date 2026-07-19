"""Graph state + run-result types for the LangGraph triage flow (Week 5).

`TriageState` is the shared channel dict the graph nodes read and write. It is a
plain `TypedDict` (no LangGraph reducers) — each node returns a partial dict that
overwrites the keys it owns — so this module imports without the optional
`graph` extra. `GraphRunResult` is a langgraph-free view of a run/resume result
that callers (tests, the PR-4 SSE endpoint) consume without touching LangGraph
snapshot types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict

from sentinel.models.incident import IncidentSummary
from sentinel.models.raw_alert import RawAlert
from sentinel.retrieval.models import RetrievedChunk


class TriageState(TypedDict, total=False):
    """Channels flowing through the triage graph.

    `total=False` so every node may return just the subset of keys it updates.

    Keys:
        correlation_id: Stable id for the run; also the checkpointer thread_id.
        raw_alert: The input alert (RawAlert or a raw dict), normalized by ingest.
        alert: Validated RawAlert produced by the ingest node.
        runbook_chunks: Retrieved runbook chunks (None when nothing matched).
        enrichment_block: Rendered CMDB enrichment text for the prompt.
        owner_team: Owning team resolved during enrichment (for the diagnosis view).
        summary: IncidentSummary produced by diagnose.
        route: "auto" or "needs_human" — recorded by diagnose for observability.
        requires_human_approval: Whether the run must pass the human gate.
        approval_status: "pending" | "auto" | "approved" | "rejected".
        human_decision: "approve"/"reject", injected before resume.
        human_note: Optional free-text note supplied with the decision.
        final_summary: The finalized IncidentSummary (only set once finalize runs).
    """

    correlation_id: str
    raw_alert: RawAlert | dict[str, Any]
    alert: RawAlert
    runbook_chunks: list[RetrievedChunk] | None
    enrichment_block: str | None
    owner_team: str | None
    summary: IncidentSummary | None
    route: str
    requires_human_approval: bool
    approval_status: str
    human_decision: str | None
    human_note: str | None
    final_summary: IncidentSummary | None


@dataclass
class GraphRunResult:
    """A langgraph-free snapshot of a triage run or resume.

    Attributes:
        correlation_id: The run's thread_id.
        interrupted: True when the graph paused before the human gate (i.e. the
            checkpoint still has a pending next node).
        values: The current state channel values.
    """

    correlation_id: str
    interrupted: bool
    values: dict[str, Any] = field(default_factory=dict)

    @property
    def summary(self) -> IncidentSummary | None:
        """Diagnosis produced before the gate (present even while interrupted)."""
        return self.values.get("summary")

    @property
    def final_summary(self) -> IncidentSummary | None:
        """Finalized summary — None until finalize runs (i.e. while interrupted)."""
        return self.values.get("final_summary")

    @property
    def approval_status(self) -> str:
        return self.values.get("approval_status", "pending")

    @property
    def owner_team(self) -> str | None:
        return self.values.get("owner_team")

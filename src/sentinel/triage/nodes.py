"""LangGraph triage nodes — each wraps an existing sentinel module.

These functions are the graph's units of work. They are deliberately
LangGraph-free (they take a plain `TriageState` dict and a `TriageDeps` bundle
and return a partial-state dict), so they import and unit-test without the
optional `graph` extra. `graph.py` binds each to its `deps` and wires the edges.

Node → module it wraps:
    ingest    → RawAlert validation
    retrieve  → engine.retrieve_runbook_context (RAG)
    enrich    → tools.enrichment.gather_context (CMDB)
    diagnose  → engine.diagnose_incident (prompt + LLM)
    human_gate→ applies the human approval decision (registered as "approve")
    finalize  → assembles the final IncidentSummary

The confidence/remediation policy lives here (`CONFIDENCE_THRESHOLD`,
`needs_human_review`) as the single source of truth; `graph.route_on_confidence`
reads it.

Observability: when `deps.emit` is set, each node emits a `node_start` frame and
the granular events the SSE demo streams (tool_call/tool_result via
gather_context, rag_query/rag_results translated from the retriever's collector,
llm_call/diagnosis from diagnose, finalized from finalize). With no emitter
(headless/tests) the nodes are silent and behave exactly as in PR 3.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sentinel.config import Settings
from sentinel.models.graph_state import TriageState
from sentinel.models.incident import IncidentSummary
from sentinel.models.raw_alert import RawAlert
from sentinel.observability.trace import EventType, TraceCollector
from sentinel.tools.enrichment import gather_context
from sentinel.triage.engine import diagnose_incident, retrieve_runbook_context

if TYPE_CHECKING:
    from sentinel.retrieval.retriever import RunbookRetriever
    from sentinel.tools.enrichment import EnrichmentToolProvider

# Auto-approve only when the model is at least this confident.
CONFIDENCE_THRESHOLD = 0.80

# (event, data) sink for surfacing node progress as SSE-shaped frames.
EventEmitter = Callable[[str, dict], None]


@dataclass
class TriageDeps:
    """Runtime dependencies injected into every node by `build_graph`.

    Kept out of `TriageState` because these are live services, not serializable
    channel data. `emit`, when provided, receives node progress + granular
    events; leave it None for a silent headless run.
    """

    settings: Settings
    retriever: RunbookRetriever | None = None
    tool_provider: EnrichmentToolProvider | None = None
    emit: EventEmitter | None = None


def _emit(deps: TriageDeps, event: str, data: dict) -> None:
    if deps.emit is not None:
        deps.emit(event, data)


def needs_human_review(summary: IncidentSummary) -> bool:
    """Whether a diagnosis must go through the human gate.

    True when the model is below the confidence threshold, or when it produced
    no grounded remediation to act on (acceptance: high-confidence but
    `proposed_remediation is None` is still forced to human review).
    """
    return (
        summary.confidence < CONFIDENCE_THRESHOLD
        or summary.proposed_remediation is None
    )


def build_initial_state(alert: RawAlert | dict, correlation_id: str) -> TriageState:
    """Seed the graph's state channels for a fresh run."""
    return {
        "raw_alert": alert,
        "correlation_id": correlation_id,
        "approval_status": "pending",
        "requires_human_approval": True,
    }


def ingest(state: TriageState, deps: TriageDeps) -> dict:
    """Normalize the input into a validated RawAlert."""
    _emit(deps, "node_start", {"node": "ingest"})
    raw = state["raw_alert"]
    alert = raw if isinstance(raw, RawAlert) else RawAlert(**raw)
    return {"alert": alert}


async def enrich(state: TriageState, deps: TriageDeps) -> dict:
    """CMDB enrichment — resolve owner/deploys and render the prompt block."""
    _emit(deps, "node_start", {"node": "enrich"})
    if deps.tool_provider is None:
        return {"enrichment_block": None, "owner_team": None}
    # gather_context ignores a None on_event, so this stays silent when unset.
    result = await gather_context(
        state["alert"], deps.tool_provider, on_event=deps.emit
    )
    return {
        "enrichment_block": result.enrichment_block,
        "owner_team": result.owner_team,
    }


def retrieve(state: TriageState, deps: TriageDeps) -> dict:
    """RAG step — fetch relevant runbook chunks (None when nothing matches)."""
    _emit(deps, "node_start", {"node": "retrieve"})
    collector = TraceCollector(verbose=False) if deps.emit is not None else None
    chunks = retrieve_runbook_context(
        state["alert"], deps.retriever, collector=collector
    )
    if collector is not None:
        _emit_rag_events(deps, collector)
    return {"runbook_chunks": chunks}


def diagnose(state: TriageState, deps: TriageDeps) -> dict:
    """LLM diagnosis — produce the IncidentSummary and decide the route."""
    _emit(deps, "node_start", {"node": "diagnose"})
    _emit(deps, "llm_call", {"model": deps.settings.model.default})

    t0 = time.perf_counter()
    summary = diagnose_incident(
        state["alert"],
        deps.settings,
        runbook_chunks=state.get("runbook_chunks"),
        enrichment_block=state.get("enrichment_block"),
    )
    duration_ms = int((time.perf_counter() - t0) * 1000)
    needs = needs_human_review(summary)

    _emit(
        deps,
        "diagnosis",
        {
            "severity": summary.severity.value,
            "service": summary.service,
            "suspected_root_cause": summary.suspected_root_cause,
            "assigned_team": state.get("owner_team"),
            "confidence": summary.confidence,
            "recommended_action": summary.suggested_urgency.value,
            "proposed_remediation": summary.proposed_remediation,
            "requires_human_approval": needs,
            "completion_tokens": None,
            "total_duration_ms": duration_ms,
        },
    )
    return {
        "summary": summary,
        "requires_human_approval": needs,
        "route": "needs_human" if needs else "auto",
    }


def human_gate(state: TriageState, deps: TriageDeps) -> dict:
    """Apply the human decision. Registered as the "approve" node; the graph
    interrupts *before* it, so it runs only after a decision is injected on
    resume."""
    _emit(deps, "node_start", {"node": "approve"})
    decision = (state.get("human_decision") or "").strip().lower()
    if decision in ("approve", "approved"):
        return {"approval_status": "approved"}
    if decision in ("reject", "rejected"):
        return {"approval_status": "rejected"}
    # Reached the gate with no usable decision — stay pending (never auto-approve
    # something that was routed for human review).
    return {"approval_status": "pending"}


def finalize(state: TriageState, deps: TriageDeps) -> dict:
    """Assemble the final IncidentSummary from the diagnosis + approval outcome.

    - Auto path (never gated): approval_status -> "auto", requires_human False.
    - Approved: remediation preserved as actionable.
    - Rejected: remediation cleared so it is not surfaced as actionable.
    """
    _emit(deps, "node_start", {"node": "finalize"})
    summary = state["summary"]
    status = state.get("approval_status", "pending")
    route = state.get("route", "auto")

    # Only the ungated (auto) path may auto-approve on a still-"pending" status.
    if route != "needs_human" and status == "pending":
        status = "auto"

    requires_human = status != "auto"
    final = summary.model_copy(
        update={
            "approval_status": status,
            "requires_human_approval": requires_human,
            "proposed_remediation": (
                None if status == "rejected" else summary.proposed_remediation
            ),
        }
    )
    _emit(
        deps,
        "finalized",
        {
            "approval_status": status,
            "requires_human_approval": requires_human,
            "proposed_remediation": final.proposed_remediation,
        },
    )
    return {
        "final_summary": final,
        "approval_status": status,
        "requires_human_approval": requires_human,
    }


def _emit_rag_events(deps: TriageDeps, collector: TraceCollector) -> None:
    """Translate the retriever's collector events into rag_query/rag_results."""
    for ev in collector.events:
        if ev.event_type == EventType.RAG_QUERY_STARTED:
            _emit(deps, "rag_query", {"query": ev.metadata.get("query")})
        elif ev.event_type == EventType.RAG_QUERY_COMPLETED:
            _emit(
                deps,
                "rag_results",
                {
                    "chunks_returned": ev.metadata.get("chunks_returned"),
                    "duration_ms": ev.metadata.get("latency_ms"),
                },
            )

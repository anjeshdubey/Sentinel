"""Core triage engine — orchestrates the full alert → IncidentSummary pipeline.

`triage_alert` is the frozen linear entry point (Weeks 1–4). Week 5 splits its
two heavy steps into `retrieve_runbook_context` and `diagnose_incident` so the
LangGraph `retrieve`/`diagnose` nodes reuse the exact same code the linear path
runs — `triage_alert` now just composes those two. `run_triage_graph` /
`resume_triage_graph` are the graph entry points; they import LangGraph lazily so
this module (and the package) still import when the optional `graph` extra is
absent.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sentinel.config import Settings
from sentinel.models.graph_state import GraphRunResult
from sentinel.models.incident import IncidentSummary
from sentinel.models.raw_alert import RawAlert
from sentinel.retrieval.models import RetrievedChunk
from sentinel.retrieval.query_builder import _guess_service, build_retrieval_query
from sentinel.triage.extractor import extract_incident
from sentinel.triage.prompts import SYSTEM_PROMPT, build_user_prompt
from sentinel.utils.hashing import generate_incident_id, hash_payload

if TYPE_CHECKING:
    from sentinel.observability.trace import TraceCollector
    from sentinel.retrieval.retriever import RunbookRetriever
    from sentinel.triage.nodes import TriageDeps


def retrieve_runbook_context(
    alert: RawAlert,
    retriever: RunbookRetriever | None,
    collector: TraceCollector | None = None,
) -> list[RetrievedChunk] | None:
    """Retrieve runbook chunks for an alert (RAG step of triage).

    Returns None when there is no retriever (Month 1 behavior) or when the
    retriever finds nothing — matching the linear pipeline's original semantics.
    """
    if retriever is None:
        return None
    query_text = build_retrieval_query(
        raw_payload=alert.raw_payload,
        metadata=alert.metadata,
    )
    service_hint = _guess_service(alert.raw_payload, alert.metadata)
    retrieval_result = retriever.query(
        text=query_text,
        service_filter=service_hint,
        collector=collector,
    )
    return retrieval_result.chunks if retrieval_result.chunks else None


def diagnose_incident(
    alert: RawAlert,
    settings: Settings,
    runbook_chunks: list[RetrievedChunk] | None = None,
    enrichment_block: str | None = None,
    collector: TraceCollector | None = None,
) -> IncidentSummary:
    """Build the prompt, call the LLM, and assemble the IncidentSummary.

    This is the "triage core": everything after retrieval. Deterministic
    identifiers are derived from the alert, so calling this with the same alert
    always yields the same incident_id/hash regardless of LLM output.
    """
    # Deterministic identifiers
    alert_hash = hash_payload(alert.raw_payload)
    incident_id = generate_incident_id(alert_hash)

    # Build prompt (with or without retrieval context + enrichment)
    user_prompt = build_user_prompt(
        source=alert.source.value,
        timestamp=alert.timestamp.isoformat(),
        raw_payload=json.dumps(alert.raw_payload, indent=2, default=str),
        metadata=json.dumps(alert.metadata, indent=2, default=str),
        runbook_context=runbook_chunks,
        enrichment_block=enrichment_block,
    )

    # LLM extraction
    if collector:
        from sentinel.observability.trace import EventType

        collector.emit(EventType.LLM_CALL_STARTED, model=settings.model.default)

    extraction = extract_incident(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        model=settings.model.default,
        temperature=settings.model.temperature,
        max_tokens=settings.model.max_tokens,
        provider=settings.model.provider,
    )

    if collector:
        from sentinel.observability.trace import EventType

        collector.emit(
            EventType.LLM_CALL_COMPLETED,
            model=settings.model.default,
            service=extraction.service,
            severity=extraction.severity.value if extraction.severity else None,
        )
        collector.emit(
            EventType.EXTRACTION_COMPLETED,
            title=extraction.title[:80] if extraction.title else None,
            confidence=extraction.confidence,
        )

    # Combine into full IncidentSummary
    # service_recognized is computed in model_post_init based on KNOWN_SERVICES
    return IncidentSummary(
        incident_id=incident_id,
        title=extraction.title,
        severity=extraction.severity,
        service=extraction.service,
        environment=extraction.environment,
        symptom=extraction.symptom,
        blast_radius=extraction.blast_radius,
        confidence=extraction.confidence,
        suspected_root_cause=extraction.suspected_root_cause,
        raw_alert_hash=alert_hash,
        timestamp=alert.timestamp,
        triage_timestamp=datetime.now(UTC),
        suggested_urgency=extraction.suggested_urgency,
        tags=extraction.tags,
        proposed_remediation=extraction.proposed_remediation,
        # requires_human_approval / approval_status keep their model defaults
        # (True / "pending"); the LangGraph approval flow sets them (Week 5, PR 4).
    )


def triage_alert(
    alert: RawAlert,
    settings: Settings,
    retriever: RunbookRetriever | None = None,
    enrichment_block: str | None = None,
    collector: TraceCollector | None = None,
) -> IncidentSummary:
    """Process a single raw alert through the linear triage pipeline.

    Steps:
    1. (Optional) Retrieve relevant runbook context via RAG
    2. Build prompt, call the LLM, and assemble the IncidentSummary

    Signature is frozen (Weeks 1–4 callers depend on it); the body now composes
    the two reusable steps the graph nodes also call.

    Args:
        alert: Validated RawAlert input.
        settings: Sentinel configuration.
        retriever: Optional RunbookRetriever for RAG context injection.
            When None, triage runs without retrieval (Month 1 behavior).
        enrichment_block: Optional pre-rendered enrichment text from tool
            provider (ownership, deploys, dependencies). Injected into
            the <enrichment> section of the prompt.
        collector: Optional TraceCollector for event emission.

    Returns:
        Complete IncidentSummary ready for output.
    """
    runbook_chunks = retrieve_runbook_context(alert, retriever, collector)
    return diagnose_incident(
        alert,
        settings,
        runbook_chunks=runbook_chunks,
        enrichment_block=enrichment_block,
        collector=collector,
    )


async def run_triage_graph(
    alert: RawAlert,
    deps: TriageDeps,
    *,
    correlation_id: str,
    graph: object | None = None,
) -> tuple[object, GraphRunResult]:
    """Run one alert through the triage graph to completion or the human gate.

    LangGraph is imported lazily so importing this module never requires the
    optional `graph` extra.

    Args:
        alert: The alert to triage.
        deps: Runtime dependencies (settings, retriever, tool provider).
        correlation_id: Stable run id, used as the checkpointer thread_id.
        graph: Optional pre-built compiled graph. Pass the same instance to
            `resume_triage_graph` so the in-process MemorySaver checkpoint is
            shared; when omitted a fresh graph (and checkpointer) is built.

    Returns:
        (compiled_graph, GraphRunResult). `interrupted` is True when the run
        paused before the human gate.
    """
    from sentinel.triage.graph import build_graph
    from sentinel.triage.nodes import build_initial_state

    compiled = graph if graph is not None else build_graph(deps)
    config = {"configurable": {"thread_id": correlation_id}}
    await compiled.ainvoke(build_initial_state(alert, correlation_id), config)
    snapshot = compiled.get_state(config)
    result = GraphRunResult(
        correlation_id=correlation_id,
        interrupted=bool(snapshot.next),
        values=dict(snapshot.values),
    )
    return compiled, result


async def resume_triage_graph(
    graph: object,
    *,
    correlation_id: str,
    human_decision: str,
    human_note: str | None = None,
) -> GraphRunResult:
    """Resume a paused triage run with a human decision.

    Must be given the same compiled `graph` returned by `run_triage_graph` so
    the shared in-process checkpoint (thread_id == correlation_id) is found.

    Args:
        graph: The compiled graph the run was started on.
        correlation_id: The paused run's thread_id.
        human_decision: "approve" or "reject".
        human_note: Optional free-text note recorded with the decision.

    Returns:
        GraphRunResult after finalize (interrupted=False on success).
    """
    config = {"configurable": {"thread_id": correlation_id}}
    graph.update_state(
        config,
        {"human_decision": human_decision, "human_note": human_note},
    )
    await graph.ainvoke(None, config)
    snapshot = graph.get_state(config)
    return GraphRunResult(
        correlation_id=correlation_id,
        interrupted=bool(snapshot.next),
        values=dict(snapshot.values),
    )

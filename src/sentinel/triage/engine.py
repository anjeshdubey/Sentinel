"""Core triage engine — orchestrates the full alert → IncidentSummary pipeline."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sentinel.config import Settings
from sentinel.models.incident import IncidentSummary
from sentinel.models.raw_alert import RawAlert
from sentinel.retrieval.query_builder import build_retrieval_query, _guess_service
from sentinel.triage.extractor import extract_incident
from sentinel.triage.prompts import SYSTEM_PROMPT, build_user_prompt
from sentinel.utils.hashing import generate_incident_id, hash_payload

if TYPE_CHECKING:
    from sentinel.observability.trace import TraceCollector
    from sentinel.retrieval.retriever import RunbookRetriever


def triage_alert(
    alert: RawAlert,
    settings: Settings,
    retriever: RunbookRetriever | None = None,
    enrichment_block: str | None = None,
    collector: TraceCollector | None = None,
) -> IncidentSummary:
    """Process a single raw alert through the triage pipeline.

    Steps:
    1. Compute deterministic hash and incident ID
    2. (Optional) Retrieve relevant runbook context via RAG
    3. Build prompt from alert data + retrieved context
    4. Call LLM for structured extraction
    5. Combine LLM output with computed fields
    6. Return validated IncidentSummary

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
    # Step 1: Deterministic identifiers
    alert_hash = hash_payload(alert.raw_payload)
    incident_id = generate_incident_id(alert_hash)

    # Step 2: Optional retrieval
    retrieval_chunks = None
    if retriever is not None:
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
        retrieval_chunks = retrieval_result.chunks if retrieval_result.chunks else None

    # Step 3: Build prompt (with or without retrieval context + enrichment)
    user_prompt = build_user_prompt(
        source=alert.source.value,
        timestamp=alert.timestamp.isoformat(),
        raw_payload=json.dumps(alert.raw_payload, indent=2, default=str),
        metadata=json.dumps(alert.metadata, indent=2, default=str),
        runbook_context=retrieval_chunks,
        enrichment_block=enrichment_block,
    )

    # Step 4: LLM extraction
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

    # Step 5: Combine into full IncidentSummary
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

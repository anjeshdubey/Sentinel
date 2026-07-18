"""Demo API endpoints: /scenarios, /triage/stream, /health.

Imports from the sentinel package (triage engine, tools, retrieval) but adds
nothing to it — this is the public-demo composition layer only.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.guardrails import (
    ALLOWED_SCENARIO_IDS,
    budget_tracker,
    is_valid_scenario_id,
)
from sentinel.config import load_settings
from sentinel.models.raw_alert import RawAlert
from sentinel.observability.trace import EventType, TraceCollector
from sentinel.retrieval.bootstrap import build_retriever
from sentinel.tools.bootstrap import build_tool_provider
from sentinel.tools.enrichment import gather_context
from sentinel.triage.engine import triage_alert

router = APIRouter()

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
CACHE_DIR = Path(__file__).resolve().parent / ".cache" / "traces"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

SENTINEL_VERSION = "1.0.0"

SCENARIOS = [
    {
        "id": "checkout-deploy",
        "title": "🔴 Checkout down after deploy",
        "severity_hint": "critical",
        "description": "Critical outage correlated with recent deployment",
        "why_interesting": "Shows deploy correlation + ownership lookup",
    },
    {
        "id": "slack-vague",
        "title": "🟡 Vague Slack report",
        "severity_hint": "warning",
        "description": "Ambiguous user report with missing context",
        "why_interesting": "Demonstrates handling of low-signal alerts",
    },
    {
        "id": "latency-no-deploy",
        "title": "🟢 Latency warning, no deploy",
        "severity_hint": "warning",
        "description": "Performance degradation without infrastructure changes",
        "why_interesting": "Clean negative case — no deploy found in time window",
    },
    {
        "id": "past-incident-match",
        "title": "🔵 Matches past incident",
        "severity_hint": "warning",
        "description": "Similar symptoms to a previously resolved incident",
        "why_interesting": "Showcases RAG retrieval from incident history",
    },
]

FIXTURE_FILES = {
    "checkout-deploy": "checkout-deploy.json",
    "slack-vague": "slack-vague.json",
    "latency-no-deploy": "latency-no-deploy.json",
    "past-incident-match": "past-incident-match.json",
}

# --- Lazily-built shared singletons (settings, retriever, tool provider) ----

_settings = None
_retriever = None
_tool_provider = None


def _get_settings():
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def _get_retriever():
    global _retriever
    if _retriever is None:
        _retriever = build_retriever(_get_settings())
    return _retriever


def _get_tool_provider():
    global _tool_provider
    if _tool_provider is None:
        settings = _get_settings()
        retriever = _get_retriever()
        _tool_provider = build_tool_provider(
            settings,
            retrieval_client=retriever._store,  # noqa: SLF001 — reuse embedded Qdrant store
            embedder=retriever._embedder,  # noqa: SLF001
        )
    return _tool_provider


class TriageRequest(BaseModel):
    scenario_id: str


def _cache_path(scenario_id: str) -> Path:
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    return CACHE_DIR / f"{scenario_id}-{date_str}.json"


def _load_fixture(scenario_id: str) -> dict:
    fixture_path = FIXTURES_DIR / FIXTURE_FILES[scenario_id]
    return json.loads(fixture_path.read_text())


def _sse_frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _run_triage_and_collect_events(scenario_id: str) -> list[dict]:
    """Execute real triage for a scenario, returning the ordered SSE event list."""
    events: list[dict] = []

    def emit(event: str, data: dict) -> None:
        events.append({"event": event, "data": data})

    fixture = _load_fixture(scenario_id)
    emit(
        "alert",
        {
            "source": fixture["source"],
            "timestamp": fixture["timestamp"],
            "payload": fixture["raw_payload"],
        },
    )

    alert = RawAlert(
        source=fixture["source"],
        timestamp=fixture["timestamp"],
        raw_payload=fixture["raw_payload"],
        metadata=fixture.get("metadata", {}),
    )

    settings = _get_settings()
    tool_provider = _get_tool_provider()
    retriever = _get_retriever()

    enrichment = await gather_context(alert, tool_provider, on_event=emit)
    enrichment_block = enrichment.enrichment_block

    # --- RAG + LLM via triage_alert (collector emits rag_query/rag_results) ---
    collector = TraceCollector(verbose=False)

    emit("llm_call", {"model": settings.model.default})

    t_total = time.perf_counter()
    summary = triage_alert(
        alert,
        settings,
        retriever=retriever,
        enrichment_block=enrichment_block,
        collector=collector,
    )
    total_duration_ms = int((time.perf_counter() - t_total) * 1000)

    # Translate collected engine events into rag_query / rag_results SSE events.
    rag_query_text = None
    for ev in collector.events:
        if ev.event_type == EventType.RAG_QUERY_STARTED:
            rag_query_text = ev.metadata.get("query")
            emit("rag_query", {"query": rag_query_text})
        elif ev.event_type == EventType.RAG_QUERY_COMPLETED:
            emit(
                "rag_results",
                {
                    "chunks_returned": ev.metadata.get("chunks_returned"),
                    "duration_ms": ev.metadata.get("latency_ms"),
                },
            )

    owner_team = enrichment.owner_team

    emit(
        "diagnosis",
        {
            "severity": summary.severity.value,
            "service": summary.service,
            "suspected_root_cause": summary.suspected_root_cause,
            "assigned_team": owner_team,
            "confidence": summary.confidence,
            "recommended_action": summary.suggested_urgency.value,
            "completion_tokens": None,
            "total_duration_ms": total_duration_ms,
        },
    )

    emit("done", {})

    return events


@router.get("/scenarios")
async def get_scenarios() -> list[dict]:
    return SCENARIOS


@router.get("/health")
async def health() -> dict:
    return {
        "status": "healthy",
        "version": SENTINEL_VERSION,
        "budget_used_pct": budget_tracker.used_pct(),
        "cache_hit_rate_24h": 0.0,
    }


@router.post("/triage/stream")
async def triage_stream(payload: TriageRequest, request: Request) -> StreamingResponse:
    scenario_id = payload.scenario_id

    if not is_valid_scenario_id(scenario_id):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid scenario_id. Must be one of: {sorted(ALLOWED_SCENARIO_IDS)}",
        )

    if budget_tracker.is_exhausted():
        raise HTTPException(
            status_code=503,
            detail={
                "error": "budget_exhausted",
                "message": "Demo has reached monthly API budget.",
                "cached_traces_available": True,
            },
        )

    cache_path = _cache_path(scenario_id)
    cache_hit = cache_path.exists()

    if cache_hit:
        events = json.loads(cache_path.read_text())
    else:
        try:
            events = await _run_triage_and_collect_events(scenario_id)
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "execution_failed",
                    "message": f"Triage execution failed: {e}",
                    "fallback_trace_id": None,
                },
            ) from e
        cache_path.write_text(json.dumps(events, default=str))

    async def event_generator():
        yield _sse_frame(
            "start",
            {
                "scenario_id": scenario_id,
                "cache_hit": cache_hit,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
        for ev in events:
            yield _sse_frame(ev["event"], ev["data"])

    return StreamingResponse(event_generator(), media_type="text/event-stream")

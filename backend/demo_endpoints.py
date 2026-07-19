"""Demo API endpoints: /scenarios, /triage/stream, /triage/resume, /health.

Imports from the sentinel package (triage graph, tools, retrieval) but adds
nothing to it — this is the public-demo composition layer only.

Week 5: /triage/stream drives the LangGraph triage graph. High-confidence runs
stream straight through to `done`; low-confidence runs pause at the human gate
and stream an `interrupt` frame carrying a `correlation_id`. The frontend POSTs
that id back to /triage/resume with the human decision, which finalizes the run.

The compiled graph's MemorySaver checkpointer is an in-process singleton so a
paused run is resumable only within the same warm process (Modal pins one warm
container for the demo; a persistent checkpointer is Week 9). When the paused
thread can't be found, /triage/resume returns 409 "session expired" rather than
500 so the demo degrades gracefully.
"""

from __future__ import annotations

import json
import uuid
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
from sentinel.retrieval.bootstrap import build_retriever
from sentinel.tools.bootstrap import build_tool_provider

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

VALID_DECISIONS = frozenset({"approve", "reject"})

# --- Lazily-built shared singletons (settings, retriever, tool provider) ----

_settings = None
_retriever = None
_tool_provider = None
_checkpointer = None


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


def _get_checkpointer():
    """In-process MemorySaver shared across per-request graph builds so a paused
    /triage/stream run is resumable by /triage/resume within the same process."""
    global _checkpointer
    if _checkpointer is None:
        from langgraph.checkpoint.memory import MemorySaver

        _checkpointer = MemorySaver()
    return _checkpointer


def _build_request_graph(emit):
    """Build a compiled graph bound to a per-request event sink, sharing the
    process-wide checkpointer so run/resume see the same threads."""
    from sentinel.triage.graph import build_graph
    from sentinel.triage.nodes import TriageDeps

    deps = TriageDeps(
        settings=_get_settings(),
        retriever=_get_retriever(),
        tool_provider=_get_tool_provider(),
        emit=emit,
    )
    return deps, build_graph(deps, checkpointer=_get_checkpointer())


class TriageRequest(BaseModel):
    scenario_id: str


class ResumeRequest(BaseModel):
    scenario_id: str
    correlation_id: str
    decision: str
    note: str | None = None


class _SessionExpired(Exception):
    """The paused thread for a resume is gone (process restart / multi-container)."""


def _cache_path(scenario_id: str) -> Path:
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    return CACHE_DIR / f"{scenario_id}-{date_str}.json"


def _resume_cache_path(scenario_id: str, decision: str) -> Path:
    date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    return CACHE_DIR / f"{scenario_id}-{decision}-{date_str}.json"


def _load_fixture(scenario_id: str) -> dict:
    fixture_path = FIXTURES_DIR / FIXTURE_FILES[scenario_id]
    return json.loads(fixture_path.read_text())


def _sse_frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _run_graph_and_collect_events(
    scenario_id: str, correlation_id: str
) -> list[dict]:
    """Drive the triage graph for a scenario, returning the ordered SSE events.

    Ends with `done` when the run auto-approves, or `interrupt` (carrying the
    correlation_id) when it pauses at the human gate.
    """
    from sentinel.triage.engine import run_triage_graph

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

    deps, graph = _build_request_graph(emit)
    _, result = await run_triage_graph(
        alert, deps, correlation_id=correlation_id, graph=graph
    )

    if result.interrupted:
        summary = result.summary
        emit(
            "interrupt",
            {
                "correlation_id": correlation_id,
                "title": summary.title,
                "severity": summary.severity.value,
                "service": summary.service,
                "confidence": summary.confidence,
                "suspected_root_cause": summary.suspected_root_cause,
                "proposed_remediation": summary.proposed_remediation,
                "assigned_team": result.owner_team,
            },
        )
    else:
        emit("done", {})

    return events


async def _resume_graph_and_collect_events(
    correlation_id: str, decision: str, note: str | None
) -> list[dict]:
    """Resume a paused run with the human decision, returning the SSE events.

    Raises _SessionExpired when no paused thread matches correlation_id.
    """
    from sentinel.triage.engine import resume_triage_graph

    events: list[dict] = []

    def emit(event: str, data: dict) -> None:
        events.append({"event": event, "data": data})

    _deps, graph = _build_request_graph(emit)
    config = {"configurable": {"thread_id": correlation_id}}
    snapshot = graph.get_state(config)
    # An unknown or already-finalized thread has no pending next node.
    if not snapshot.next:
        raise _SessionExpired

    await resume_triage_graph(
        graph, correlation_id=correlation_id, human_decision=decision, human_note=note
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
        correlation_id = f"{scenario_id}:{uuid.uuid4().hex}"
        try:
            events = await _run_graph_and_collect_events(scenario_id, correlation_id)
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


@router.post("/triage/resume")
async def triage_resume(payload: ResumeRequest, request: Request) -> StreamingResponse:
    scenario_id = payload.scenario_id

    if not is_valid_scenario_id(scenario_id):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid scenario_id. Must be one of: {sorted(ALLOWED_SCENARIO_IDS)}",
        )

    decision = payload.decision.strip().lower()
    if decision not in VALID_DECISIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid decision. Must be one of: {sorted(VALID_DECISIONS)}",
        )

    cache_path = _resume_cache_path(scenario_id, decision)
    cache_hit = cache_path.exists()

    if cache_hit:
        events = json.loads(cache_path.read_text())
    else:
        try:
            events = await _resume_graph_and_collect_events(
                payload.correlation_id, decision, payload.note
            )
        except _SessionExpired as e:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "session_expired",
                    "message": "Approval session expired — re-run the scenario.",
                    "correlation_id": payload.correlation_id,
                },
            ) from e
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail={"error": "resume_failed", "message": f"Resume failed: {e}"},
            ) from e
        cache_path.write_text(json.dumps(events, default=str))

    async def event_generator():
        yield _sse_frame(
            "start",
            {
                "scenario_id": scenario_id,
                "resumed": True,
                "decision": decision,
                "cache_hit": cache_hit,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
        for ev in events:
            yield _sse_frame(ev["event"], ev["data"])

    return StreamingResponse(event_generator(), media_type="text/event-stream")

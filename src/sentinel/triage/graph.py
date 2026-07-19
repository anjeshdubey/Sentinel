"""LangGraph assembly for the HITL triage flow (Week 5).

This is the only module that imports LangGraph, so it is imported lazily by
`engine.run_triage_graph` — the rest of `sentinel` (and the package import) works
without the optional `graph` extra installed.

Topology::

    START → ingest → enrich → retrieve → diagnose ─(route_on_confidence)─┐
                                                                          │
                              ┌──────────── "finalize" (auto-approve) ────┤
                              │                                           │
                              │   "approve" ── interrupt_before ──▶ approve (human_gate)
                              ▼                                           │
                           finalize ◀──────────────────────────────────── ┘ → END

`enrich` runs before `retrieve` so, when the SSE endpoint (PR 4) drives the
graph, tool-call events surface ahead of RAG events — matching today's demo.
The gate node is registered under the name ``"approve"`` and the graph is
compiled with ``interrupt_before=["approve"]``, so a run routed for human review
pauses there until `resume_triage_graph` injects the decision.
"""

from __future__ import annotations

from functools import partial

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from sentinel.models.graph_state import TriageState
from sentinel.triage.nodes import (
    CONFIDENCE_THRESHOLD,
    TriageDeps,
    diagnose,
    enrich,
    finalize,
    human_gate,
    ingest,
    needs_human_review,
    retrieve,
)

__all__ = ["CONFIDENCE_THRESHOLD", "build_graph", "route_on_confidence"]

# Node name for the human approval gate; the graph interrupts before it.
APPROVE_NODE = "approve"


def route_on_confidence(state: TriageState) -> str:
    """Conditional edge after `diagnose`: auto-approve vs. human gate.

    Returns the name of the next node — "finalize" for the auto path,
    "approve" (the human gate) when the diagnosis needs review.
    """
    return APPROVE_NODE if needs_human_review(state["summary"]) else "finalize"


def build_graph(deps: TriageDeps, checkpointer=None):
    """Build and compile the triage graph with a checkpointer.

    Args:
        deps: Runtime dependencies bound into every node.
        checkpointer: Optional checkpointer to compile with. Pass a shared
            MemorySaver so freshly-built graph instances (e.g. one per SSE
            request, each with its own `deps.emit`) resolve the same paused
            thread on resume. Omit for a private per-graph MemorySaver.

    Returns:
        A compiled graph with a checkpointer and ``interrupt_before=["approve"]``.
    """
    builder = StateGraph(TriageState)

    builder.add_node("ingest", partial(ingest, deps=deps))
    builder.add_node("enrich", partial(enrich, deps=deps))
    builder.add_node("retrieve", partial(retrieve, deps=deps))
    builder.add_node("diagnose", partial(diagnose, deps=deps))
    builder.add_node(APPROVE_NODE, partial(human_gate, deps=deps))
    builder.add_node("finalize", partial(finalize, deps=deps))

    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "enrich")
    builder.add_edge("enrich", "retrieve")
    builder.add_edge("retrieve", "diagnose")
    builder.add_conditional_edges(
        "diagnose",
        route_on_confidence,
        {APPROVE_NODE: APPROVE_NODE, "finalize": "finalize"},
    )
    builder.add_edge(APPROVE_NODE, "finalize")
    builder.add_edge("finalize", END)

    return builder.compile(
        checkpointer=checkpointer if checkpointer is not None else MemorySaver(),
        interrupt_before=[APPROVE_NODE],
    )

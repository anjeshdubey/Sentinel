# 0002. Use LangGraph for the human-in-the-loop triage flow

**Status**: Accepted
**Date**: 2026-07-18

## Context

Through Week 4, triage was a single linear function (`triage_alert()`): retrieve
runbooks, build the prompt, call the LLM, assemble an `IncidentSummary`. Week 5
adds a human approval gate — a low-confidence (or un-grounded) diagnosis must
pause, wait for an operator to approve or reject, and only then finalize. That
introduces a control-flow requirement the linear function can't express: a run
has to **suspend mid-flight, persist its state, and resume later** on a separate
HTTP request.

Constraints in play:

- The existing `triage_alert()` signature is depended on by Weeks 1–4 callers
  and their tests (the baseline that must stay green), so it had to stay frozen.
- The pause/resume state has to survive between a `POST /triage/stream` and a
  later `POST /triage/resume`.
- The demo runs on a tight budget and a simple deploy (Modal / GitHub Pages);
  we did not want to stand up external infrastructure (a queue, a database) for
  this.
- The package must still import for callers that don't use the graph, without
  forcing a new heavy dependency on them.

## Decision

Model triage as a small [LangGraph](https://langchain-ai.github.io/langgraph/)
state machine (`ingest → enrich → retrieve → diagnose → [approve] → finalize`)
compiled with a `MemorySaver` checkpointer and `interrupt_before=["approve"]`.

- **Reuse, don't fork.** `triage_alert()`'s two heavy steps were extracted into
  `retrieve_runbook_context()` and `diagnose_incident()`; both the frozen linear
  function and the graph's `retrieve`/`diagnose` nodes call them, so there is one
  implementation of the triage core, not two. Likewise the CMDB enrichment was
  lifted into `tools/enrichment.gather_context()`, shared by the SSE endpoint and
  the `enrich` node.
- **Routing policy is single-sourced.** `CONFIDENCE_THRESHOLD = 0.80` and
  `needs_human_review()` live in one place; `route_on_confidence` reads them.
- **Lazy dependency.** LangGraph is an optional `graph` extra imported lazily by
  the graph entry points, so `import sentinel...` still works without it.
- **In-process checkpointer for now.** `MemorySaver` keeps the demo dependency
  free. Resume must land in the same warm process; when it can't, the endpoint
  returns `409 session_expired` rather than a 500. A cross-process persistent
  checkpointer is deferred until it's actually needed.

## Consequences

The approval gate is expressed declaratively (a conditional edge + an
`interrupt_before`) instead of as hand-rolled suspend/resume plumbing, and each
node is independently unit-testable against a plain state dict. The linear path
is untouched, so the Weeks 1–4 baseline stays green.

The costs we consciously accepted: a new optional dependency (LangGraph) and its
learning curve; a **warm-process coupling** for resume that is genuinely fragile
under multi-container / cold-start deploys (mitigated, not solved, by the `409`
and by pinning one warm container for the demo — see
[Deployment § Warm-process resume](../deployment.md#warm-process-resume)); and a
known LangGraph-1.x msgpack deprecation warning when the checkpointer serializes
our Pydantic state types, to be addressed when the persistent checkpointer lands.

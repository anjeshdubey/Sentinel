"""Langfuse-backed tracer — forwards triage events as spans on one trace.

Lazily imports the `langfuse` SDK so importing `sentinel...` never requires the
optional `observability` extra (same pattern as the `graph` extra's lazy
langgraph import in `triage/engine.py`).

One `LangfuseTracer` is built per triage run, seeded with the run's
`correlation_id` so every span it logs (one per `.log()` call — nodes call
this once per `node_start` / `tool_call` / `diagnosis` / ... event) lands on
the same Langfuse trace, and a `/triage/resume` call for the same
`correlation_id` continues that trace rather than starting a new one.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sentinel.config import ObservabilityConfig

logger = logging.getLogger(__name__)


class LangfuseTracer:
    """Forwards `TraceCollector`/node events to Langfuse as spans.

    Exposes the `.log(name, payload=...)` interface `TraceCollector._forward_to_tracer`
    already expects (see `observability/trace.py`), so it plugs into the
    existing hook without changing that module.
    """

    def __init__(self, config: ObservabilityConfig, correlation_id: str) -> None:
        from langfuse import Langfuse
        from langfuse.types import TraceContext

        self._client = Langfuse(
            public_key=config.langfuse_public_key,
            secret_key=config.langfuse_secret_key,
            host=config.langfuse_host,
            flush_at=config.flush_at,
            flush_interval=config.flush_interval,
        )
        trace_id = self._client.create_trace_id(seed=correlation_id)
        self._trace_context = TraceContext(trace_id=trace_id)

    def log(self, name: str, *, payload: dict[str, Any]) -> None:
        """Record one span on this run's trace. Never raises."""
        try:
            span = self._client.start_observation(
                trace_context=self._trace_context,
                name=name,
                as_type="span",
                output=payload,
            )
            span.end()
        except Exception:
            logger.warning("Langfuse span logging failed for %r", name, exc_info=True)

    def flush(self) -> None:
        """Force-send buffered spans. Call at the end of a request — batching
        means spans may otherwise not be sent before a serverless container
        freezes/exits."""
        try:
            self._client.flush()
        except Exception:
            logger.warning("Langfuse flush failed", exc_info=True)


def build_tracer(
    config: ObservabilityConfig, correlation_id: str
) -> LangfuseTracer | None:
    """Build a tracer when Langfuse is enabled and configured; None otherwise.

    Never raises — a broken Langfuse config degrades to no tracing rather than
    breaking the triage run.
    """
    if not config.langfuse_enabled:
        return None
    if not config.langfuse_public_key or not config.langfuse_secret_key:
        logger.warning(
            "observability.langfuse_enabled is True but public/secret key is "
            "missing — tracing disabled for this run."
        )
        return None
    try:
        return LangfuseTracer(config, correlation_id)
    except Exception:
        logger.warning("Failed to initialize Langfuse tracer", exc_info=True)
        return None

"""Lightweight trace event system for the Sentinel triage pipeline.

Emits structured events that can be:
- Logged locally (for debugging via --verbose)
- Streamed as SSE events (for Day 7 backend)
- Forwarded to Langfuse (when observability.langfuse_enabled=True)

Design:
- TraceEvent: immutable dataclass representing a single pipeline event
- TraceCollector: accumulates events during a triage run, optionally logs them
- Event emission is opt-in: if no collector is provided, zero overhead
- Each TraceCollector gets a unique trace_id for event correlation
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    SERVICE_EXTRACTED = "service_extracted"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    RAG_QUERY_STARTED = "rag_query_started"
    RAG_QUERY_COMPLETED = "rag_query_completed"
    LLM_CALL_STARTED = "llm_call_started"
    LLM_CALL_COMPLETED = "llm_call_completed"
    EXTRACTION_COMPLETED = "extraction_completed"


@dataclass(frozen=True)
class TraceEvent:
    """A single trace event from the triage pipeline."""

    event_type: EventType
    timestamp_ms: int
    trace_id: str
    span_id: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_sse(self) -> str:
        """Format as an SSE event line for streaming."""
        import json

        data = {
            "event": self.event_type.value,
            "ts": self.timestamp_ms,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            **self.metadata,
        }
        return f"data: {json.dumps(data)}\n\n"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON logging."""
        d = asdict(self)
        d["event_type"] = self.event_type.value
        return d


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_span_id() -> str:
    return uuid.uuid4().hex[:16]


class TraceCollector:
    """Accumulates trace events during a single triage run.

    Each collector is assigned a unique trace_id at creation time,
    which correlates all events emitted during one triage invocation.
    Each event also gets a unique span_id for fine-grained tracking.

    Usage:
        collector = TraceCollector(verbose=True)
        collector.emit(EventType.SERVICE_EXTRACTED, service_name="checkout-api")
        ...
        events = collector.events  # list of all events
        trace_id = collector.trace_id  # correlation ID for this run
    """

    def __init__(self, verbose: bool = False, trace_id: str | None = None) -> None:
        self._events: list[TraceEvent] = []
        self._verbose = verbose
        self._trace_id = trace_id or uuid.uuid4().hex
        self._tracer = None

    @property
    def trace_id(self) -> str:
        return self._trace_id

    @property
    def events(self) -> list[TraceEvent]:
        return list(self._events)

    def attach_tracer(self, tracer: Any) -> None:
        """Attach the Langfuse tracer for forwarding events as spans.

        When attached, each emitted event is also forwarded to Langfuse
        as a span observation for production observability.
        """
        self._tracer = tracer

    def emit(self, event_type: EventType, **metadata: Any) -> TraceEvent:
        """Emit a trace event.

        Args:
            event_type: The type of event.
            **metadata: Arbitrary key-value metadata for the event.

        Returns:
            The created TraceEvent.
        """
        span_id = _new_span_id()
        event = TraceEvent(
            event_type=event_type,
            timestamp_ms=_now_ms(),
            trace_id=self._trace_id,
            span_id=span_id,
            metadata=metadata,
        )
        self._events.append(event)

        if self._verbose:
            logger.info(
                "[trace:%s] %s span=%s %s",
                self._trace_id[:8],
                event_type.value,
                span_id[:8],
                metadata,
            )

        if self._tracer is not None:
            self._forward_to_tracer(event)

        return event

    def _forward_to_tracer(self, event: TraceEvent) -> None:
        """Forward event to the attached Langfuse tracer as a log entry."""
        try:
            self._tracer.log(event.event_type.value, payload=event.metadata)
        except Exception:
            pass

    def elapsed_since(self, start_event: TraceEvent) -> int:
        """Calculate milliseconds elapsed since a previous event."""
        return _now_ms() - start_event.timestamp_ms

    def summary(self) -> dict[str, Any]:
        """Return a summary of collected events for logging."""
        by_type: dict[str, int] = {}
        for e in self._events:
            by_type[e.event_type.value] = by_type.get(e.event_type.value, 0) + 1
        total_ms = 0
        if len(self._events) >= 2:
            total_ms = self._events[-1].timestamp_ms - self._events[0].timestamp_ms
        return {
            "trace_id": self._trace_id,
            "event_count": len(self._events),
            "by_type": by_type,
            "total_ms": total_ms,
        }

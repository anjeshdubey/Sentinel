"""Sentinel observability — Langfuse integration with zero-overhead fallback.

This package provides:
- LangfuseTracer: Production tracer with ContextVar-based span stack
- NullTracer: Zero-overhead no-op tracer when Langfuse is disabled
- get_tracer(): Singleton factory returning the appropriate tracer
- trace_span(): Decorator for automatic span creation
- set_trace_context(): FastAPI middleware integration helper
- PII redaction for span metadata
- TraceCollector / TraceEvent: Lightweight event emission for pipeline steps
"""

from sentinel.observability.trace import EventType, TraceCollector, TraceEvent
from sentinel.observability.tracer import (
    LangfuseTracer,
    NullTracer,
    get_tracer,
    set_trace_context,
    trace_span,
)

__all__ = [
    "EventType",
    "LangfuseTracer",
    "NullTracer",
    "TraceCollector",
    "TraceEvent",
    "get_tracer",
    "set_trace_context",
    "trace_span",
]

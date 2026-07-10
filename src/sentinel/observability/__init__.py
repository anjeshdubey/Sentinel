"""Sentinel observability — lightweight event emission for demo.

This package provides:
- TraceCollector: Event accumulation for SSE streaming
- TraceEvent: Structured event with trace_id and span_id
- EventType: Enum of all event types (service extraction, tools, RAG, LLM)
"""

from sentinel.observability.trace import EventType, TraceCollector, TraceEvent

__all__ = [
    "EventType",
    "TraceCollector",
    "TraceEvent",
]

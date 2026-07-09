"""Langfuse observability integration for Sentinel triage pipeline.

Provides:
- LangfuseTracer: Production tracer wrapping the Langfuse SDK with a
  ContextVar-based span stack for concurrent safety.
- NullTracer: Zero-overhead no-op tracer with identical async context
  manager interface.
- get_tracer(): Singleton factory (lazy init).
- trace_span(): Decorator for automatic span creation (identity function
  when disabled — zero overhead, no import errors if langfuse not installed).
- set_trace_context(): Sets trace_id in ContextVar for FastAPI middleware.

Feature flag: LANGFUSE_ENABLED env var (default: "false").
When disabled, no Langfuse imports are attempted, no network calls made.

Span stack uses ContextVar[list] (not class-level list) for concurrent
safety across asyncio tasks and Month 5 parallel workers.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import os
import re
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, AsyncIterator, Callable, Dict, Optional

# Feature flag — evaluated lazily so tests can patch the env var after import.
# The module-level constant is kept for backward compatibility but the
# authoritative check is _is_langfuse_enabled() which re-reads the env var.
LANGFUSE_ENABLED = os.getenv("LANGFUSE_ENABLED", "false").lower() == "true"


def _is_langfuse_enabled() -> bool:
    """Re-evaluate LANGFUSE_ENABLED from env (allows test patching)."""
    return os.getenv("LANGFUSE_ENABLED", "false").lower() == "true"


def _is_langfuse_available() -> bool:
    """Check if the langfuse SDK can be imported."""
    try:
        import langfuse  # noqa: F401
        return True
    except ImportError:
        return False


# Conditional import: if Langfuse not installed, gracefully degrade
LANGFUSE_AVAILABLE = False
if LANGFUSE_ENABLED:
    try:
        from langfuse import Langfuse  # type: ignore[import-untyped]

        LANGFUSE_AVAILABLE = True
    except ImportError:
        LANGFUSE_AVAILABLE = False

# Context var for trace_id propagation (FastAPI middleware sets this)
_trace_context: ContextVar[Optional[str]] = ContextVar(
    "sentinel_trace_context", default=None
)


# --- PII Redaction ---

# Key-name based redaction (any metadata key matching these is scrubbed)
_SENSITIVE_KEYS = frozenset({
    "payload",
    "raw_alert",
    "raw_payload",
    "message",
    "description",
    "api_key",
    "token",
    "secret",
    "password",
    "authorization",
    "cookie",
    "session",
})

# Regex patterns for secrets embedded in string values
_SECRET_PATTERNS = [
    re.compile(r"sk-ant-api03-[A-Za-z0-9_\-]{20,}"),  # Anthropic API keys
    re.compile(r"xox[bpars]-[A-Za-z0-9\-]{10,}"),  # Slack tokens
    re.compile(
        r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE
    ),  # Bearer tokens
    re.compile(
        r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"
    ),  # JWT
    re.compile(r"Basic\s+[A-Za-z0-9+/]+=*", re.IGNORECASE),  # HTTP Basic
    re.compile(
        r"00D[A-Za-z0-9]{12,15}![A-Za-z0-9._]+"
    ),  # Salesforce session tokens
]

# Maximum string length before truncation
_MAX_STRING_LENGTH = 200


def _redact_pii(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Redact PII, secrets, and long strings from span metadata.

    Rules:
    1. Keys matching SENSITIVE_KEYS are replaced with type placeholder.
    2. String values are scanned against SECRET_PATTERNS and redacted.
    3. Strings exceeding 200 chars are truncated with length indicator.
    4. Nested dicts are recursively processed.

    Args:
        metadata: Raw metadata dict from caller.

    Returns:
        Scrubbed copy of metadata (original is not mutated).
    """
    redacted: Dict[str, Any] = {}
    for k, v in metadata.items():
        if k.lower() in _SENSITIVE_KEYS:
            redacted[k] = f"<redacted {type(v).__name__}>"
        elif isinstance(v, str):
            scrubbed = v
            for pattern in _SECRET_PATTERNS:
                scrubbed = pattern.sub("<REDACTED_SECRET>", scrubbed)
            if len(scrubbed) > _MAX_STRING_LENGTH:
                redacted[k] = (
                    f"{scrubbed[:50]}... <truncated, len={len(scrubbed)}>"
                )
            else:
                redacted[k] = scrubbed
        elif isinstance(v, dict):
            redacted[k] = _redact_pii(v)
        elif isinstance(v, list):
            redacted[k] = [
                _redact_pii(item) if isinstance(item, dict) else item
                for item in v
            ]
        else:
            redacted[k] = v
    return redacted


# --- NullTracer ---


class NullTracer:
    """No-op tracer when Langfuse is disabled.

    Exposes the SAME async context-manager protocol as LangfuseTracer.span()
    because callers use `async with tracer.span(...)`. A sync contextmanager
    here would raise TypeError at runtime when LANGFUSE_ENABLED=false (the
    default). Kept async for interface parity.

    All methods are no-ops with zero allocation overhead.
    """

    @asynccontextmanager
    async def span(
        self, name: str, metadata: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[NullTracer]:
        """No-op span context manager."""
        yield self

    def set_attribute(self, key: str, value: Any) -> None:
        """No-op."""

    def set_status(self, status: str) -> None:
        """No-op."""

    def log(self, event: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """No-op."""

    def flush(self) -> None:
        """No-op."""


# --- LangfuseTracer ---


class LangfuseTracer:
    """Production tracer wrapping Langfuse SDK with ContextVar span stack.

    The span stack is stored in a ContextVar so that:
    - Each asyncio task gets its own stack (concurrent safety).
    - Nested spans correctly link to their parent.
    - Month 5 parallel workers sharing a ToolProvider see independent stacks.

    Raises:
        RuntimeError: If Langfuse SDK is not installed.
    """

    def __init__(
        self,
        public_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        host: Optional[str] = None,
        flush_at: int = 100,
        flush_interval: float = 5.0,
    ) -> None:
        if not LANGFUSE_AVAILABLE:
            raise RuntimeError(
                "Langfuse SDK not installed. "
                "Install with: pip install langfuse"
            )

        self._client = Langfuse(
            public_key=public_key or os.getenv("LANGFUSE_PUBLIC_KEY"),
            secret_key=secret_key or os.getenv("LANGFUSE_SECRET_KEY"),
            host=host or os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
            flush_at=flush_at,
            flush_interval=flush_interval,
        )
        # ContextVar for concurrent-safe span stack
        self._span_stack: ContextVar[list[Any]] = ContextVar(
            "langfuse_span_stack", default=[]
        )

    @asynccontextmanager
    async def span(
        self, name: str, metadata: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[Any]:
        """Async context manager for span creation.

        Creates a span linked to the current trace (from trace_context ContextVar)
        and parent span (from span stack). Automatically records latency on exit.

        Args:
            name: Span name (e.g., "triage.classify").
            metadata: Optional metadata dict (PII-redacted before sending).

        Yields:
            The Langfuse span object for attribute/status setting.
        """
        trace_id = _trace_context.get()
        stack = self._span_stack.get()
        parent_span = stack[-1] if stack else None

        start_time = time.perf_counter()
        scrubbed_metadata = _redact_pii(metadata) if metadata else {}

        span_obj = self._client.span(
            name=name,
            trace_id=trace_id,
            parent_observation_id=(
                parent_span.id if parent_span and hasattr(parent_span, "id") else None
            ),
            metadata=scrubbed_metadata,
        )

        # Push onto stack (immutable update for ContextVar safety)
        new_stack = stack + [span_obj]
        self._span_stack.set(new_stack)

        try:
            yield span_obj
        except Exception as e:
            span_obj.end(status_message=f"error: {type(e).__name__}: {str(e)[:100]}")
            raise
        finally:
            latency_ms = (time.perf_counter() - start_time) * 1000
            span_obj.end(metadata={"latency_ms": round(latency_ms, 2)})
            # Pop from stack
            current_stack = self._span_stack.get()
            self._span_stack.set(current_stack[:-1])

    def set_attribute(self, key: str, value: Any) -> None:
        """Add attribute to the current (top-of-stack) span."""
        stack = self._span_stack.get()
        if stack:
            stack[-1].update(metadata={key: value})

    def set_status(self, status: str) -> None:
        """Set status message on the current span."""
        stack = self._span_stack.get()
        if stack:
            stack[-1].update(status_message=status)

    def log(self, event: str, payload: Optional[Dict[str, Any]] = None) -> None:
        """Log an event on the current span."""
        stack = self._span_stack.get()
        if stack and payload:
            stack[-1].update(metadata={f"event_{event}": _redact_pii(payload)})

    def flush(self) -> None:
        """Flush pending spans to Langfuse."""
        self._client.flush()


# --- Singleton ---

_tracer_instance: Optional[LangfuseTracer | NullTracer] = None


def get_tracer() -> LangfuseTracer | NullTracer:
    """Get the singleton tracer instance (lazy initialization).

    Returns LangfuseTracer when LANGFUSE_ENABLED=true and the SDK is
    installed. Otherwise returns NullTracer (zero overhead).

    Note: Uses _is_langfuse_enabled() which re-reads the env var at call
    time, so tests can patch LANGFUSE_ENABLED via os.environ after import
    and then call reset_tracer() to pick up the change.
    """
    global _tracer_instance
    if _tracer_instance is None:
        if _is_langfuse_enabled() and _is_langfuse_available():
            _tracer_instance = LangfuseTracer()
        else:
            _tracer_instance = NullTracer()
    return _tracer_instance


def reset_tracer() -> None:
    """Reset the singleton (for testing only)."""
    global _tracer_instance
    _tracer_instance = None


# --- Decorator ---


def trace_span(
    name: str,
    metadata_fn: Optional[Callable[..., Dict[str, Any]]] = None,
) -> Callable[..., Any]:
    """Decorator for automatic span creation around functions.

    When LANGFUSE_ENABLED=false, returns the function unchanged (identity
    decorator — zero overhead, no wrapping, no import errors).

    Args:
        name: Span name (e.g., "triage.classify").
        metadata_fn: Optional callable that receives the same args as the
                     decorated function and returns a metadata dict.

    Example:
        @trace_span("triage.classify", metadata_fn=lambda alert: {"source": alert.source})
        async def classify_alert(alert: RawAlert) -> Classification:
            ...
    """
    if not _is_langfuse_enabled():
        # Zero-overhead no-op when disabled — return identity
        def _identity(f: Callable[..., Any]) -> Callable[..., Any]:
            return f

        return _identity

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                tracer = get_tracer()
                metadata = (
                    metadata_fn(*args, **kwargs) if metadata_fn else {}
                )
                async with tracer.span(name, metadata=metadata):
                    return await func(*args, **kwargs)

            return async_wrapper
        else:
            # Sync functions: create a span by running the async context
            # manager via asyncio. If an event loop is already running
            # (e.g., inside an async caller), fall back to direct execution
            # since nested asyncio.run() is not allowed.
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                tracer = get_tracer()
                metadata = (
                    metadata_fn(*args, **kwargs) if metadata_fn else {}
                )

                async def _traced() -> Any:
                    async with tracer.span(name, metadata=metadata):
                        return func(*args, **kwargs)

                try:
                    asyncio.get_running_loop()
                    # Already inside an event loop — cannot use asyncio.run().
                    # Fall back to untraced execution (caller should use async
                    # path for full instrumentation).
                    return func(*args, **kwargs)
                except RuntimeError:
                    # No running loop — safe to use asyncio.run()
                    return asyncio.run(_traced())

            return sync_wrapper

    return decorator


# --- Context helpers ---


def set_trace_context(trace_id: str) -> None:
    """Set trace_id in ContextVar (called by FastAPI middleware).

    This allows all spans created within the request to be linked
    to the same trace.
    """
    _trace_context.set(trace_id)


def get_trace_context() -> Optional[str]:
    """Get the current trace_id from ContextVar."""
    return _trace_context.get()

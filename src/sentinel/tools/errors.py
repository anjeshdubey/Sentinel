"""Tool error hierarchy — the error contract for all tool backends.

Callers catch ToolError at the enrichment boundary and render "(unavailable)"
in the enrichment block. The engine never sees a raw exception from a tool.

Hierarchy:
    ToolError (base — catch to fail-open)
    +-- ToolTimeout (backend exceeded per-tool timeout)
    +-- ToolBackendError (transport, parse, auth, 5xx)
    +-- ToolNotFound (resource required but missing)
    +-- ToolNotImplemented (Month-5 stubs, distinct from backend errors)
"""


class ToolError(Exception):
    """Base for all tool errors. Callers catch ToolError to fail-open."""


class ToolTimeout(ToolError):
    """Backend did not respond within the per-tool timeout.

    Attributes:
        tool: Name of the tool that timed out.
        timeout_ms: The configured timeout in milliseconds.
    """

    def __init__(self, tool: str, timeout_ms: int) -> None:
        super().__init__(f"{tool} exceeded {timeout_ms}ms timeout")
        self.tool = tool
        self.timeout_ms = timeout_ms


class ToolBackendError(ToolError):
    """Backend raised (transport, parse, auth, 5xx). The call may be retried.

    Attributes:
        tool: Name of the tool that errored.
        cause: Human-readable description of the failure.
    """

    def __init__(self, tool: str, cause: str) -> None:
        super().__init__(f"{tool} backend error: {cause}")
        self.tool = tool
        self.cause = cause


class ToolNotFound(ToolError):
    """Distinct from 'None' returns.

    Used only when the caller REQUIRES the resource (e.g., submit_resolution
    against an unknown incident_id).
    """


class ToolNotImplemented(ToolError):
    """Month-5 stubs raise this in Month 2.

    Distinct from ToolBackendError so the engine can render
    '(available in Month 5)' instead of '(unavailable)'.
    """

    def __init__(self, tool: str, reason: str = "Not yet implemented") -> None:
        super().__init__(f"{tool}: {reason}")
        self.tool = tool
        self.reason = reason

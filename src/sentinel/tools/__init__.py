"""Sentinel tool abstractions — async-first protocol layer for context enrichment.

This package provides:
- Protocol interfaces for six tool backends (ownership, deploys, deps, past incidents,
  resolution recording, escalation advice)
- Domain models for tool results (ServiceOwner, Deploy, PastIncident, etc.)
- ToolProvider container with per-tool timeouts, caching, and single-flight dedup
- SyncToolProvider facade for CLI usage
- JSON backend implementations (fake CMDB)
- Stubs for Month-5 tools
- Context formatting for prompt injection
"""

from sentinel.tools.errors import (
    ToolBackendError,
    ToolError,
    ToolNotFound,
    ToolNotImplemented,
    ToolTimeout,
)
from sentinel.tools.models import (
    Deploy,
    EnrichmentContext,
    EscalationAdvice,
    EscalationTarget,
    PastIncident,
    ResolutionRecord,
    ServiceDependencies,
    ServiceOwner,
)
from sentinel.tools.protocols import (
    PROTOCOL_VERSION,
    DeploysProvider,
    DependenciesProvider,
    EscalationAdvisor,
    OwnershipProvider,
    PastIncidentsProvider,
    ResolutionRecorder,
)

__all__ = [
    # Models
    "Deploy",
    "EnrichmentContext",
    "EscalationAdvice",
    "EscalationTarget",
    "PastIncident",
    "ResolutionRecord",
    "ServiceDependencies",
    "ServiceOwner",
    # Protocols
    "DeploysProvider",
    "DependenciesProvider",
    "EscalationAdvisor",
    "OwnershipProvider",
    "PastIncidentsProvider",
    "PROTOCOL_VERSION",
    "ResolutionRecorder",
    # Errors
    "ToolBackendError",
    "ToolError",
    "ToolNotFound",
    "ToolNotImplemented",
    "ToolTimeout",
]

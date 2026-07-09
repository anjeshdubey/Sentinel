"""Domain models for tool results — the data contract between backends and callers.

These models are intentionally backend-agnostic: they do NOT leak implementation
details (e.g., cosine scores) into callers. See PastIncident for the
rank+bucket pattern that enables backend swaps without caller changes.

All models are Pydantic BaseModel instances for runtime validation and
serialization.
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from sentinel.models.enums import Severity

# Relevance bucket type — all backends can produce this without leaking scores.
Relevance = Literal["strong", "moderate", "weak"]


class ServiceOwner(BaseModel):
    """Ownership record for a service.

    The `aliases` field enables service-name resolution: when an alert says
    "checkout" but the CMDB knows "checkout-api", the tool provider resolves
    via alias scan (exact match first, then iterate aliases).
    """

    service: str
    team: str
    tier: int = Field(ge=0, le=3, description="0 = highest criticality")
    escalation_channel: str = Field(description="Slack channel or PagerDuty rotation")
    manager: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)


class Deploy(BaseModel):
    """A single deploy event for a service."""

    service: str
    version: str
    deployed_at: datetime
    deployed_by: str
    change_summary: Optional[str] = None
    rollback_of: Optional[str] = None


class ServiceDependencies(BaseModel):
    """Upstream and downstream dependency graph for a service."""

    service: str
    upstream: list[str] = Field(default_factory=list)
    downstream: list[str] = Field(default_factory=list)


class PastIncident(BaseModel):
    """Domain type for a similar past incident.

    NOTE: No `similarity: float` field. The ranking is what matters, the score
    is an implementation detail. Rank + relevance bucket is the contract.

    The Qdrant-backed provider maps cosine scores to relevance buckets:
        >= 0.75 -> strong
        >= 0.50 -> moderate
        < 0.50  -> weak

    A future real-backend that returns a categorical rank maps directly.
    Callers (formatter, LLM prompt, eval assertions) MUST NOT depend on
    score being a float.
    """

    incident_id: str
    title: str
    service: str
    severity: Severity
    resolved_summary: str
    occurred_at: datetime
    rank: int = Field(ge=1, description="1..k, always populated by the provider")
    relevance: Relevance = Field(description="Bucketed relevance — strong/moderate/weak")


class EscalationTarget(BaseModel):
    """Discriminated union for escalation routing.

    type determines the channel kind; value is the specific identifier.
    Examples:
        EscalationTarget(type="slack", value="#payments-oncall")
        EscalationTarget(type="pagerduty", value="payments-rotation")
        EscalationTarget(type="email", value="sre-lead@example.com")
    """

    type: Literal["slack", "pagerduty", "email"]
    value: str


class EscalationAdvice(BaseModel):
    """Return type for get_escalation_advice — Month 5 stub, defined now."""

    target: EscalationTarget
    urgency: Literal["immediate", "next_hour", "next_day", "monitor"]
    tier: int = Field(ge=0, le=3)
    reason: str = Field(description="Human-readable justification for the advice")


class ResolutionRecord(BaseModel):
    """Payload for submit_resolution — Month 5 stub, defined now."""

    incident_id: str
    resolution_summary: str
    resolved_at: datetime
    resolved_by: str
    root_cause_category: Optional[str] = None


class EnrichmentContext(BaseModel):
    """Aggregated enrichment data from the four Month 2 tool calls.

    Populated by the enrichment step; rendered by format_context_block().
    """

    service_owner: Optional[ServiceOwner] = None
    recent_deploys: list[Deploy] = Field(default_factory=list)
    past_incidents: list[PastIncident] = Field(default_factory=list)
    service_dependencies: Optional[ServiceDependencies] = None

    # Tracking fields for observability
    unavailable_tools: list[str] = Field(
        default_factory=list,
        description="Tool names that raised ToolError during enrichment",
    )

"""Incident summary output model — the core contract of Sentinel."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from sentinel.models.enums import Severity, Urgency

# Known services in Sentinel's infrastructure.
# This list grows as the synthetic data generator adds services.
# Used for soft validation only — unknown services are accepted but flagged.
KNOWN_SERVICES: set[str] = {
    "checkout",
    "payments",
    "inventory",
    "auth",
    "api-gateway",
    "order-service",
    "notification-service",
    "search",
    "recommendation-engine",
    "flow-engine",
    "flow-orchestrator",
}


class IncidentSummary(BaseModel):
    """Structured triage output produced by the LLM extraction step.

    This is Sentinel's primary output contract. Downstream consumers
    (LangGraph nodes, JSONL files, SQLite, APIs) all receive this shape.
    """

    incident_id: str = Field(description="Deterministic UUID derived from alert content hash")
    title: str = Field(
        max_length=120,
        description="Human-readable summary of the incident",
    )
    severity: Severity = Field(
        description=(
            "Incident severity. Use 'critical' for full outages or >50% error rate. "
            "'high' for partial outages or >10% error rate. "
            "'medium' for degraded performance. 'low' for warnings."
        )
    )
    service: str = Field(
        description="The primary affected service, as a lowercase hyphenated slug."
    )
    service_recognized: bool = Field(
        default=True,
        description="Whether the extracted service is in the known services list.",
    )
    environment: str = Field(description="Environment: prod, staging, dev, sandbox, unknown")
    symptom: str = Field(
        description=(
            "One sentence describing what is failing and how, in plain English. "
            "Example: 'Payment gateway returning HTTP 500 at 87% error rate.'"
        )
    )
    blast_radius: list[str] = Field(
        default_factory=list,
        description=(
            "List of affected regions, tenants, or user groups. "
            "Use strings like 'us-east-1', 'eu-west-1', 'all-regions', "
            "'enterprise-tier', 'free-tier'. Empty list if scope is unknown."
        ),
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "How confident you are in this classification, from 0.0 to 1.0. "
            "Use >0.8 only when the alert is clear and unambiguous. "
            "Use 0.3–0.6 for noisy or incomplete alert data."
        ),
    )
    suspected_root_cause: Optional[str] = Field(
        default=None,
        description=(
            "If the alert clearly suggests a cause (recent deploy, DB timeout, "
            "memory leak), state it in one sentence. None if genuinely unclear."
        ),
    )
    raw_alert_hash: str = Field(description="SHA-256 of the input RawAlert for traceability")
    timestamp: datetime = Field(description="When the original alert fired")
    triage_timestamp: datetime = Field(description="When Sentinel processed this alert")
    suggested_urgency: Urgency = Field(description="Recommended response urgency")
    tags: list[str] = Field(
        default_factory=list,
        description="Freeform classification tags (e.g., 'database', 'latency', 'OOM')",
    )

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        if not v.strip():
            msg = "Title cannot be empty or whitespace-only"
            raise ValueError(msg)
        return v.strip()

    @field_validator("service")
    @classmethod
    def normalize_service(cls, v: str) -> str:
        """Normalize service name to lowercase hyphenated slug."""
        return v.lower().strip().replace(" ", "-").replace("_", "-")

    @field_validator("confidence")
    @classmethod
    def round_confidence(cls, v: float) -> float:
        """Round confidence to 2 decimal places."""
        return round(v, 2)

    @field_validator("tags")
    @classmethod
    def lowercase_tags(cls, v: list[str]) -> list[str]:
        return [tag.lower().strip() for tag in v if tag.strip()]

    def model_post_init(self, __context: object) -> None:
        """Set service_recognized based on KNOWN_SERVICES after validation."""
        self.service_recognized = (
            self.service in KNOWN_SERVICES or self.service == "unknown"
        )

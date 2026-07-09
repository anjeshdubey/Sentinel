"""Structured extraction via Instructor + Anthropic SDK (through LLM gateway)."""

from typing import Optional

import instructor
from pydantic import BaseModel, Field

from sentinel.gateway import GatewayConfig, create_client, resolve_model
from sentinel.models.enums import Severity, Urgency


class LLMIncidentExtraction(BaseModel):
    """Schema the LLM fills in. Excludes computed fields (IDs, hashes, timestamps).

    This is what Instructor extracts from the LLM response.
    The triage engine then enriches it with deterministic fields.
    """

    title: str = Field(max_length=120, description="Human-readable incident summary")
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
    suggested_urgency: Urgency = Field(description="Recommended response urgency")
    tags: list[str] = Field(default_factory=list, description="Classification tags")


def extract_incident(
    system_prompt: str,
    user_prompt: str,
    model: str,
    temperature: float,
    max_tokens: int,
    api_key: str | None = None,
    max_retries: int = 2,
) -> LLMIncidentExtraction:
    """Call Claude via Instructor to extract structured incident data.

    Uses the Salesforce LLM gateway (ANTHROPIC_AUTH_TOKEN + ANTHROPIC_BASE_URL)
    by default. Falls back to direct Anthropic API if ANTHROPIC_API_KEY is set
    and no gateway config is present.

    Args:
        system_prompt: System prompt with triage instructions.
        user_prompt: Rendered user prompt with alert data.
        model: Claude model identifier (short name or gateway full name).
        temperature: Sampling temperature (0.0 for deterministic).
        max_tokens: Max response tokens.
        api_key: Override auth token (optional — normally loaded from env).
        max_retries: Number of retry attempts on validation failure.

    Returns:
        Validated LLMIncidentExtraction with all LLM-determined fields.
    """
    # Create gateway-aware client
    if api_key:
        # Explicit key passed — build config manually (for testing)
        config = GatewayConfig.from_env()
        config.auth_token = api_key
    else:
        config = GatewayConfig.from_env()

    raw_client = create_client(config)
    client = instructor.from_anthropic(raw_client)

    # Resolve model name to gateway format
    resolved_model = resolve_model(model)

    return client.messages.create(
        model=resolved_model,
        max_tokens=max_tokens,
        temperature=temperature,
        max_retries=max_retries,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        response_model=LLMIncidentExtraction,
    )
